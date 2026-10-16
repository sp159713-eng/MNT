from datetime import datetime

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (CaptionLabel, CheckBox, LineEdit, PlainTextEdit,
                            PrimaryPushButton, PushButton, StrongBodyLabel)

from qtgui.chart import Chart
from qtgui.common import (DataTable, EditSpinBox, StatCard, TitledCard,
                          TabbedContainer, set_tone)

COLUMNS = (("Score", 80, Qt.AlignCenter), ("Symbol", 120, Qt.AlignLeft),
           ("Published", 130, Qt.AlignLeft), ("Headline", 400, Qt.AlignLeft))
HINT = "Click a headline to see the rest for that stock."
PULSE_NOTE = (
    "Whether conditions suit a ranking model at all. Dispersion is how far "
    "apart the names are moving - wide is good, because ranking needs winners "
    "and losers to separate. High average correlation is bad: everything "
    "moving together leaves nothing to rank. Above 50-DMA and India VIX are "
    "breadth and fear. Press Snapshot to record one reading.")
TONES = {"green": "good", "amber": "warn", "red": "bad"}


def _when(item):
    stamp = (item.get("age") or "").strip()
    if stamp:
        return stamp
    import news as news_module

    return news_module.age(item.get("published") or "")


def _score(item):
    return f"{item['score']:+d}" if item["score"] else "0"


class NewsPage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.items = []
        self.shown = []
        self.related_rows = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.query = LineEdit(self)
        self.query.setFixedWidth(240)
        self.query.setPlaceholderText("Symbol or company")
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query)
        self.search_button = PushButton("Search news", self)
        self.search_button.clicked.connect(self.search)
        row.addWidget(self.search_button)
        row.addStretch(1)
        self.auto = CheckBox("auto every", self)
        self.auto.stateChanged.connect(lambda _s: self.auto_toggle())
        row.addWidget(self.auto)
        self.every = EditSpinBox(self)
        self.every.setRange(1, 180)
        self.every.setSingleStep(5)
        self.every.setValue(15)
        self.every.setFixedWidth(130)
        row.addWidget(self.every)
        row.addWidget(CaptionLabel("min", self))
        self.fetch_button = PrimaryPushButton("Fetch", self)
        self.fetch_button.clicked.connect(self.fetch)
        row.addWidget(self.fetch_button)
        root.addLayout(row)

        self.status = CaptionLabel("", self)
        root.addWidget(self.status)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._tick)

        card = TitledCard("Headlines", "Google News RSS", self)
        self.table = DataTable(
            COLUMNS, "No headlines yet - press Fetch or search a stock",
            stretch=3, parent=card)
        self.table.table.itemSelectionChanged.connect(self._select)
        self.table.table.itemDoubleClicked.connect(lambda _i: self._open())
        card.body.addWidget(self.table, 1)
        note = CaptionLabel(
            "Sentiment is a positive-minus-negative word count. It is not a "
            "signal and nothing in this project trades on it.", card)
        note.setWordWrap(True)
        card.body.addWidget(note)
        root.addWidget(card, 3)

        self.related = TitledCard("More on this stock",
                                  "double-click any headline to open it", self)
        self.related_table = DataTable(
            COLUMNS, "Nothing selected yet", stretch=3, parent=self.related)
        self.related_table.table.itemDoubleClicked.connect(
            lambda _i: self._open_related())
        self.related.body.addWidget(self.related_table, 1)
        self.related_note = CaptionLabel(HINT, self.related)
        self.related_note.setWordWrap(True)
        self.related.body.addWidget(self.related_note)
        root.addWidget(self.related, 2)

    def auto_toggle(self):
        if self.auto.isChecked():
            self.fetch()
            self._schedule()
        else:
            self.timer.stop()

    def _schedule(self):
        if not self.auto.isChecked():
            return
        self.timer.start(max(int(self.every.value()), 1) * 60000)

    def _tick(self):
        self.fetch()
        self._schedule()

    def _say(self, text, tone="muted"):
        self.status.setText(text)
        set_tone(self.status, tone)

    def fetch(self):
        self._say("fetching...")

        def work():
            import news as news_module

            symbols = news_module._held_or_default()
            known = news_module.seen_titles()
            items = news_module.collect(symbols, limit=5, quiet=True)
            fresh = [i for i in items if i["title"] not in known]
            news_module.record(fresh)
            return items, len(fresh)

        self.app.worker.submit(work, self._done, self._failed)

    def _done(self, payload):
        items, fresh = payload
        self.items = items
        self.related_rows = []
        self.related_table.set_rows([])
        self.related_note.setText(HINT)
        self.shown = sorted(items, key=lambda i: -i["score"])
        rows = [(_score(i), i["symbol"], _when(i), i["title"])
                for i in self.shown]
        tones = ["good" if i["score"] > 0 else "bad" if i["score"] < 0
                 else None for i in self.shown]
        self.table.set_rows(rows, tones)
        when = datetime.now().strftime("%H:%M:%S")
        suffix = f", {fresh} new" if self.auto.isChecked() else ""
        if not items:
            import config
            import news as news_module

            self._say("No stocks yet - add them on the Stock page."
                      if not config.UNIVERSE
                      else f"Nothing published in the last "
                           f"{news_module.MAX_AGE_DAYS} days  ({when})")
            return
        self._say(f"{len(items)} headlines{suffix}  ({when})")

    def search(self):
        text = self.query.text().strip()
        if not text:
            self._say("Type a symbol or company name first.")
            return
        self.search_button.setEnabled(False)
        self._say(f"Looking for news on {text}...")

        def work():
            import config
            import news as news_module

            symbol = config.valid_symbol(text)
            if symbol:
                items = news_module.fetch(symbol, limit=12)
                if items:
                    return symbol, items
            matches = config.search_symbols(text, limit=1)
            if matches and matches[0]["exchange"] != "-":
                found = matches[0]["symbol"]
                return found, news_module.fetch(found, limit=12)
            return (symbol or text.upper()), []

        self.app.worker.submit(work, self._searched, self._search_failed)

    def _searched(self, payload):
        symbol, items = payload
        self.search_button.setEnabled(True)
        self._done((items, 0))
        if items:
            self._say(f"{len(items)} headlines for {symbol}")
        else:
            self._say(f"No headlines found for {symbol}")

    def _search_failed(self, error):
        self.search_button.setEnabled(True)
        self._say(f"{type(error).__name__}: {error}"[:90], "bad")

    def _failed(self, error):
        self._say(str(error)[:80], "bad")

    def _selected_item(self):
        row = self.table.table.currentRow()
        if 0 <= row < len(self.shown):
            return self.shown[row]
        return None

    def _select(self):
        item = self._selected_item()
        if item:
            self._fill_related(item)

    def _fill_related(self, chosen):
        symbol = chosen["symbol"]
        same = [i for i in self.items
                if i["symbol"] == symbol and i["title"] != chosen["title"]]
        others = [i for i in self.items if i["symbol"] != symbol]
        rows = same + others
        self.related_rows = rows
        self.related_table.set_rows(
            [(_score(i), i["symbol"], _when(i), i["title"]) for i in rows],
            ["muted" if i["symbol"] != symbol else None for i in rows])
        self.related_note.setText(
            f"{len(same)} more on {symbol}, then {len(others)} from the "
            f"rest of the book.")

    def _launch(self, item):
        link = (item.get("link") or "").strip()
        if not link:
            self.related_note.setText("That headline carries no link.")
            return
        QDesktopServices.openUrl(QUrl(link))

    def _open(self):
        item = self._selected_item()
        if item:
            self._launch(item)

    def _open_related(self):
        row = self.related_table.table.currentRow()
        if 0 <= row < len(self.related_rows):
            self._launch(self.related_rows[row])


class PulsePage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.has_reading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        row = QHBoxLayout()
        note = CaptionLabel(PULSE_NOTE, self)
        note.setWordWrap(True)
        row.addWidget(note, 1)
        self.snapshot_button = PrimaryPushButton("Snapshot", self)
        self.snapshot_button.clicked.connect(self.snapshot)
        row.addWidget(self.snapshot_button, 0, Qt.AlignTop)
        root.addLayout(row)

        light = TitledCard("Conditions", parent=self)
        light.title.hide()
        line = QHBoxLayout()
        self.light_dot = StrongBodyLabel("●", light)
        set_tone(self.light_dot, "muted")
        line.addWidget(self.light_dot)
        self.light_text = StrongBodyLabel("No data", light)
        line.addWidget(self.light_text)
        line.addStretch(1)
        light.body.addLayout(line)
        root.addWidget(light)

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.tiles = {}
        for key, label in (("dispersion_1d", "dispersion 1d"),
                           ("avg_correlation", "avg correlation"),
                           ("pct_above_50dma", "above 50-DMA"),
                           ("vix", "India VIX")):
            tile = StatCard(label, self)
            tiles.addWidget(tile, 1)
            self.tiles[key] = tile.value
        root.addLayout(tiles)

        panes = QHBoxLayout()
        panes.setSpacing(10)
        left = TitledCard("Reading", parent=self)
        self.text = PlainTextEdit(left)
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("No reading yet - press Snapshot")
        left.body.addWidget(self.text, 1)
        panes.addWidget(left, 1)
        right = TitledCard("Recorded dispersion", "one point per snapshot",
                           self)
        self.chart = Chart(right, height=240)
        right.body.addWidget(self.chart, 1)
        panes.addWidget(right, 1)
        root.addLayout(panes, 1)

    def on_show(self):
        if not self.has_reading:
            self.snapshot()

    def snapshot(self):
        def work():
            import pulse as pulse_module

            snapshot = pulse_module.take()
            pulse_module.record(snapshot)
            return (snapshot, pulse_module.summary(snapshot),
                    pulse_module.history())

        self.app.worker.submit(work, self._done, lambda _e: None)

    def _done(self, payload):
        snapshot, summary, history = payload
        import pulse as pulse_module

        self.has_reading = bool(summary.strip())
        level, words = pulse_module.verdict(snapshot)
        set_tone(self.light_dot, TONES.get(level, "muted"))
        self.light_text.setText(words)
        for key, pattern in (("dispersion_1d", "{:.2%}"),
                             ("avg_correlation", "{:.2f}"),
                             ("pct_above_50dma", "{:.0%}"),
                             ("vix", "{:.2f}")):
            raw = snapshot.get(key)
            self.tiles[key].setText(
                pattern.format(raw) if isinstance(raw, (int, float)) else "-")
        self.text.setPlainText(summary)
        series = [r["dispersion_1d"] for r in history
                  if isinstance(r.get("dispersion_1d"), float)]
        self.chart.line(series if len(series) >= 2 else [],
                        formatter=lambda v: f"{v:.1%}")


class NewsSection(TabbedContainer):
    def __init__(self, app, parent=None):
        super().__init__(
            "News", (("News", NewsPage(app)), ("Pulse", PulsePage(app))),
            parent)
