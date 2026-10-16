from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (CaptionLabel, CheckBox, IndeterminateProgressBar,
                            LineEdit, PrimaryPushButton, themeColor)

from qtgui.chart import Chart
from qtgui.common import (EditSpinBox, StatCard, TitledCard, set_tone,
                          title_row)
from qtgui.gap_card import GapCard

TILES = (("capital", "starting capital"), ("final", "closing equity"),
         ("bench", "buy & hold"), ("perfect", "perfect bot"),
         ("charges", "charges paid"), ("dd", "max drawdown"))


class SimPage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.last_payload = None
        self.running = False
        self.setObjectName("page-Sim")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(14)

        row = title_row("Simulation", self)
        row.addWidget(CaptionLabel("capital Rs", self))
        self.capital_entry = LineEdit(self)
        self.capital_entry.setText(f"{self.app.capital:,.0f}")
        self.capital_entry.setFixedWidth(120)
        self.capital_entry.editingFinished.connect(self._apply_capital)
        row.addWidget(self.capital_entry)
        row.addWidget(CaptionLabel("sessions", self))
        self.days = EditSpinBox(self)
        self.days.setRange(60, 1500)
        self.days.setSingleStep(21)
        self.days.setValue(252)
        self.days.setFixedWidth(140)
        row.addWidget(self.days)
        self.show_ideal = CheckBox("Show perfect bot", self)
        self.show_ideal.stateChanged.connect(lambda _s: self._redraw())
        row.addWidget(self.show_ideal)
        self.run_button = PrimaryPushButton("Run sim", self)
        self.run_button.clicked.connect(self.run)
        row.addWidget(self.run_button)
        root.addLayout(row)

        self.progress = IndeterminateProgressBar(self)
        self.progress.hide()
        root.addWidget(self.progress)

        self.gap = GapCard(self)
        root.addWidget(self.gap)

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.tiles = {}
        for key, label in TILES:
            tile = StatCard(label, self)
            tiles.addWidget(tile, 1)
            self.tiles[key] = tile.value
        self.tiles["capital"].setText(f"{self.app.capital:,.0f}")
        root.addLayout(tiles)

        card = TitledCard("Equity", "traded book, marked at each close", self)
        self.chart = Chart(card, height=160)
        card.body.addWidget(self.chart, 1)
        self.verdict = CaptionLabel("Press Run sim", card)
        self.verdict.setWordWrap(True)
        card.body.addWidget(self.verdict)
        root.addWidget(card, 1)

    def on_show(self):
        try:
            self.gap.refresh()
        except Exception:
            pass

    def _read_capital(self):
        text = self.capital_entry.text().strip().replace(",", "")
        text = text.replace("_", "").replace(" ", "")
        for prefix in ("rs.", "rs", "₹"):
            if text.lower().startswith(prefix):
                text = text[len(prefix):]
                break
        try:
            amount = float(text)
        except ValueError:
            return None
        if amount <= 0 or amount > 1e12:
            return None
        return amount

    def _apply_capital(self):
        amount = self._read_capital()
        if amount is None:
            self.verdict.setText("Starting capital must be a positive number, "
                                 "for example 500000.")
            set_tone(self.verdict, "bad")
            self.capital_entry.setText(f"{self.app.capital:,.0f}")
            return False
        self.app.capital = amount
        self.capital_entry.setText(f"{amount:,.0f}")
        self.tiles["capital"].setText(f"{amount:,.0f}")
        return True

    def _busy(self, busy):
        self.running = busy
        self.run_button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.start()
        else:
            self.progress.stop()

    def run(self):
        if not self._apply_capital():
            return
        self._busy(True)
        days, capital = self.days.value(), self.app.capital

        def work():
            import config
            import paper as paper_module

            every = config.REBALANCE_EVERY
            top_k = config.TOP_K
            panel = paper_module.build_panel()
            curve = paper_module.replay(days, capital, every, top_k,
                                        quiet=True, panel=panel)
            bench = paper_module.benchmark_curve(curve["timestamp"], capital)
            stats = paper_module.summarise(curve, bench, capital)
            run_id = paper_module.record(stats, every, top_k)
            try:
                ceiling = paper_module.ceiling_curve(days, capital, every,
                                                     top_k, panel=panel)
            except Exception:
                ceiling = None
            return curve, bench, capital, stats, run_id, ceiling

        self.app.worker.submit(work, self._done, self._failed)

    def _redraw(self):
        if self.last_payload is not None and not self.running:
            self._done(self.last_payload)

    def _done(self, payload):
        self.last_payload = payload
        curve, bench, capital, stats, run_id, ceiling = payload
        self._busy(False)

        equity = curve["equity"].tolist()
        drawdown = stats["max_drawdown_pct"]
        final = stats["final_equity"]
        bench_final = stats["benchmark_equity"]
        charges = stats["charges_paid"]
        fills = stats["trades"]

        self.tiles["capital"].setText(f"{capital:,.0f}")
        self.tiles["final"].setText(f"{final:,.0f}")
        set_tone(self.tiles["final"], "good" if final >= capital else "bad")
        self.tiles["bench"].setText(f"{bench_final:,.0f}")
        self.tiles["charges"].setText(f"{charges:,.0f}")
        self.tiles["dd"].setText(f"{drawdown:.1f}%")
        set_tone(self.tiles["dd"], "bad")

        stamps = [str(t.date()) for t in curve["timestamp"]]
        labels = [stamps[0], stamps[-1]]

        ideal = []
        if ceiling is not None and len(ceiling) == len(equity):
            ideal = ceiling["equity"].tolist()
        self.tiles["perfect"].setText(f"{ideal[-1]:,.0f}" if ideal else "-")
        if ideal:
            self.tiles["perfect"].setTextColor(themeColor(), themeColor())
        else:
            set_tone(self.tiles["perfect"], "muted")

        show = self.show_ideal.isChecked()

        def readout(index, value):
            made = value - capital
            pct = (made / capital * 100.0) if capital else 0.0
            sign = "+" if made >= 0 else "-"
            tone = "good" if made >= 0 else "bad"
            text = (f"{stamps[index]}\n"
                    f"equity  {value:,.0f}\n"
                    f"made   {sign}{abs(made):,.0f}  ({sign}{abs(pct):.1f}%)")
            if ideal and show:
                text += f"\nperfect {ideal[index]:,.0f}"
            return text, tone

        ceiling_made = (ideal[-1] - capital) if ideal else 0.0
        shown = bool(ideal) and show
        self.chart.line(equity, labels=labels, baseline=capital,
                        colour="good" if final >= capital else "bad",
                        formatter=lambda v: f"{v / 1000:,.0f}k",
                        readout=readout,
                        overlay=ideal if shown else None,
                        overlay_label=(f"perfect bot {ideal[-1]:,.0f}  "
                                       f"(+{ceiling_made:,.0f})")
                        if shown else None)
        logged = f" Logged as run {run_id}." if run_id else ""
        set_tone(self.verdict, "muted")
        self.verdict.setText(
            f"{stats['sessions']} sessions, {fills} fills. "
            + ("Beat buy-and-hold." if final >= bench_final else
               f"Lost to buy-and-hold by Rs {bench_final - final:,.0f} - "
               f"charges alone were "
               f"{stats['charges_pct_capital']:.2f}% of capital.")
            + logged)

    def _failed(self, error):
        self._busy(False)
        self.verdict.setText(str(error)[:140])
        set_tone(self.verdict, "bad")
