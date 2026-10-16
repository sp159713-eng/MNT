import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, PushButton

import model_info
from qtgui.chart import Chart
from qtgui.common import DataTable, TitledCard, clear_tone, set_tone, title_row

LEFT, RIGHT, CENTER = Qt.AlignLeft, Qt.AlignRight, Qt.AlignHCenter
COLUMNS = (("Bot", 130, LEFT), ("Signal", 140, LEFT),
           ("Start", 110, CENTER), ("Equity", 100, RIGHT),
           ("Ret %", 78, RIGHT), ("Vs Nifty", 78, RIGHT),
           ("Vs EW", 78, RIGHT), ("Days", 70, RIGHT),
           ("Last run", 110, CENTER))
NOTE = ("Each paper bot runs one signal on a Rs 5 lakh account and rebalances "
        "every 20 trading days. Points are percentage points of return ahead "
        "of Nifty or an equal-weight basket since the bot started. Click a "
        "row to see that bot alone; click it again for all. Read-only - "
        "nothing here runs a bot.")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_rows():
    scripts = os.path.join(BASE_DIR, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import bots

    rows = bots.status_rows(strict=False)
    rows.sort(key=lambda r: (r["empty"], -r.get("ret", 0.0)))
    return rows


def normalised(series):
    return [v / series[0] * 100 for v in series] if series and series[0] else []


class BotsPage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setObjectName("page-Bots")
        self.rows = []
        self.selected = None
        self.loaded = False
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)

        top = title_row("Paper bots", self)
        self.status = CaptionLabel("", self)
        top.addWidget(self.status)
        self.refresh_button = PushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.refresh)
        top.addWidget(self.refresh_button)
        root.addLayout(top)

        note = CaptionLabel(NOTE, self)
        note.setWordWrap(True)
        root.addWidget(note)

        table = TitledCard("Bots", "sorted by return", self)
        self.table = DataTable(COLUMNS, "No bots yet", stretch=1, parent=table)
        self.table.setMinimumHeight(330)
        self.table.table.cellClicked.connect(self._picked)
        table.body.addWidget(self.table, 1)
        root.addWidget(table, 1)

        chart_card = TitledCard("Equity", "normalised to 100 at each bot's start",
                                self)
        self.chart = Chart(chart_card, height=200)
        chart_card.body.addWidget(self.chart, 1)
        root.addWidget(chart_card, 1)

    def on_show(self):
        if not self.loaded:
            self.refresh()

    def refresh(self):
        self.refresh_button.setEnabled(False)
        clear_tone(self.status)
        self.status.setText("reading bots...")
        self.app.worker.submit(load_rows, self._done, self._failed)

    def _done(self, rows):
        self.loaded = True
        self.refresh_button.setEnabled(True)
        self.rows = rows
        clear_tone(self.status)
        self.status.setText(f"{len(rows)} bots")
        data = []
        for r in rows:
            signal = model_info.label(r["signal"], short=True)
            if r["empty"]:
                data.append((r["name"], signal, "no runs yet",
                             "", "", "", "", "", ""))
            else:
                data.append((r["name"], signal, r["start"],
                             f"{r['equity']:,.0f}", f"{r['ret']:+.2f}",
                             f"{r['vs_nifty']:+.2f}", f"{r['vs_ew']:+.2f}",
                             "-" if r["since"] is None else r["since"],
                             r["last_run"]))
        self.table.set_rows(data)
        self.selected = None
        self._draw()

    def _failed(self, error):
        self.refresh_button.setEnabled(True)
        self.status.setText(str(error)[:80])
        set_tone(self.status, "bad")

    def _picked(self, row, _column):
        if row >= len(self.rows):
            return
        name = self.rows[row]["name"]
        if self.selected == name:
            self.selected = None
            self.table.table.clearSelection()
        else:
            self.selected = name
        self._draw()

    def _draw(self):
        live = [r for r in self.rows if not r["empty"]]
        if self.selected:
            live = [r for r in live if r["name"] == self.selected]
        series = [(r["name"], normalised(r["series"])) for r in live]
        best = max(live, key=lambda r: r["ret"])["name"] if live else None
        self.chart.lines(series, best=best, formatter=lambda v: f"{v:.1f}",
                         baseline=100.0)
