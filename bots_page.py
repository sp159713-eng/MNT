from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk

from pages import Page
from theme import Button, Card, Chart, Palette

COLUMNS = (("bot", 80, "BOT", "w"), ("signal", 170, "SIGNAL", "w"),
           ("start", 85, "START", "center"), ("equity", 80, "EQUITY", "e"),
           ("ret", 85, "RETURN %", "e"), ("nifty", 85, "VS NIFTY", "e"),
           ("ew", 90, "VS EQ-WT", "e"), ("since", 100, "REBAL DAYS", "e"),
           ("last", 95, "LAST RUN", "center"))


def load_rows():
    scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import bots

    rows = bots.status_rows(strict=False)
    rows.sort(key=lambda r: (r["empty"], -r.get("ret", 0.0)))
    return rows


def normalised(series):
    return [v / series[0] * 100 for v in series] if series and series[0] else []


class BotsPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        row = self.header("Paper bots")
        self.refresh_button = Button(row, "Refresh", self.refresh)
        self.refresh_button.pack(side="right")
        self.status = tk.Label(row, text="", bg=Palette.bg, fg=Palette.muted,
                               font=self.f["small"])
        self.status.pack(side="right", padx=12)

        tk.Label(self,
                 text="Each paper bot runs one signal on a Rs 5 lakh account "
                      "and rebalances every 20 trading days. Points are "
                      "percentage points of return ahead of Nifty or an "
                      "equal-weight basket since the bot started. Click a "
                      "row to see that bot alone; click it again for all. "
                      "Read-only - nothing here runs a bot.",
                 bg=Palette.bg, fg=Palette.faint, font=self.f["small"],
                 anchor="w", justify="left", wraplength=900).pack(
            fill="x", pady=(0, 12))

        table = Card(self, "Bots", "sorted by return")
        table.pack(fill="x", pady=(0, 12))
        self.tree = ttk.Treeview(table.body,
                                 columns=[c[0] for c in COLUMNS],
                                 show="headings", height=8,
                                 selectmode="browse")
        for key, width, heading, anchor in COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=key == "signal")
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self._picked)

        chart_card = Card(self, "Equity", "normalised to 100 at each bot's start")
        chart_card.pack(fill="both", expand=True)
        self.chart = Chart(chart_card.body, height=240)
        self.chart.pack(fill="both", expand=True)

        self.rows = []
        self.selected = None
        self.loaded = False

    def on_show(self) -> None:
        if not self.loaded:
            self.refresh()

    def refresh(self) -> None:
        self.refresh_button.set_enabled(False)
        self.status.config(text="reading bots...", fg=Palette.muted)
        self.app.worker.submit(load_rows, self._done, self._failed)

    def _done(self, rows) -> None:
        self.loaded = True
        self.refresh_button.set_enabled(True)
        self.rows = rows
        self.status.config(text=f"{len(rows)} bots")
        self.tree.delete(*self.tree.get_children())
        for index, r in enumerate(rows):
            if r["empty"]:
                values = (r["name"], r["signal"] or "momentum", "no runs yet",
                          "", "", "", "", "", "")
            else:
                values = (r["name"], r["signal"] or "momentum", r["start"],
                          f"{r['equity']:,.0f}", f"{r['ret']:+.2f}",
                          f"{r['vs_nifty']:+.2f}", f"{r['vs_ew']:+.2f}",
                          "-" if r["since"] is None else r["since"],
                          r["last_run"])
            self.tree.insert("", "end", iid=str(index), values=values)
        self.selected = None
        self._draw()

    def _failed(self, error) -> None:
        self.refresh_button.set_enabled(True)
        self.status.config(text=str(error)[:80], fg=Palette.bad)

    def _picked(self, _event=None) -> None:
        chosen = self.tree.selection()
        if not chosen:
            return
        name = self.rows[int(chosen[0])]["name"]
        if self.selected == name:
            self.selected = None
            self.tree.selection_remove(chosen)
        else:
            self.selected = name
        self._draw()

    def _draw(self) -> None:
        live = [r for r in self.rows if not r["empty"]]
        if self.selected:
            live = [r for r in live if r["name"] == self.selected]
        series = [(r["name"], normalised(r["series"])) for r in live]
        best = max(live, key=lambda r: r["ret"])["name"] if live else None
        self.chart.lines(series, best=best, formatter=lambda v: f"{v:.1f}",
                         baseline=100.0)
