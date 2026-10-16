import os
import re
import subprocess
import sys

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (CaptionLabel, CheckBox, IndeterminateProgressBar,
                            PlainTextEdit, PrimaryPushButton, PushButton,
                            RadioButton, Slider, SubtitleLabel)

from qtgui.chart import Chart
from qtgui.common import (DataTable, confirm, EditSpinBox, TabbedContainer, TitledCard,
                          set_tone)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_DIR = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
           else BASE_DIR)
RE_BP = re.compile(r"^[+-]\d+$")
COST_NOTE = (
    "What one buy-then-sell costs in charges and slippage. The big number is "
    "how far the price must move before you break even, so 34 bp means "
    "0.34%. Fixed charges do not shrink, so small positions pay "
    "proportionally more - that is what the curve shows.")
LEFT, RIGHT = Qt.AlignLeft, Qt.AlignRight


class LineBridge(QObject):
    line = Signal(str)


class BacktestPage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.report = None
        self.bridge = LineBridge(self)
        self.bridge.line.connect(self._append)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        import config as config_module

        self.presets = [f"gbm{n}" for n in
                        getattr(config_module, "GBM_PRESETS", ())]
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(SubtitleLabel("Walk-forward", self))
        top.addStretch(1)
        top.addWidget(CaptionLabel(
            "  ".join(n.replace("gbm", "GBM ") for n in self.presets), self))
        self.fast = CheckBox("fast mode", self)
        top.addWidget(self.fast)
        self.use_button = PushButton("Use best", self)
        self.use_button.clicked.connect(self.use_best)
        self.use_button.setEnabled(False)
        top.addWidget(self.use_button)
        self.auto_button = PushButton("Auto model", self)
        self.auto_button.clicked.connect(self.auto)
        top.addWidget(self.auto_button)
        self.run_button = PrimaryPushButton("Run", self)
        self.run_button.clicked.connect(self.run)
        top.addWidget(self.run_button)
        root.addLayout(top)

        self.progress = IndeterminateProgressBar(self)
        self.progress.hide()
        root.addWidget(self.progress)

        panes = QHBoxLayout()
        panes.setSpacing(10)
        left = TitledCard("Excess return by test year",
                          "basis points per period, after costs", self)
        self.chart = Chart(left, height=260)
        left.body.addWidget(self.chart, 1)
        self.scores = CaptionLabel("edge -    acc -", left)
        self.scores.setWordWrap(True)
        left.body.addWidget(self.scores)
        panes.addWidget(left, 1)
        right = TitledCard("Output", parent=self)
        self.log = PlainTextEdit(right)
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(PlainTextEdit.NoWrap)
        self.log.setPlaceholderText("Press Run to start a walk-forward")
        right.body.addWidget(self.log, 1)
        panes.addWidget(right, 1)
        root.addLayout(panes, 1)
        QTimer.singleShot(50, self._load_last)

    def _load_last(self):
        try:
            from gapcard import fold_series, latest_walkforward

            record = latest_walkforward()
            years, values = fold_series(record)
            if not values:
                return
            self.chart.lines([("walk-forward", values)], years,
                             best="walk-forward", baseline=0.0)
            pooled = record["pooled"]
            edge = record.get("edge") or {}
            self.scores.setText(
                f"run #{record['id']}  pooled "
                f"{pooled['net_excess_bp']:+.0f}bp  "
                f"t {pooled['t_stat']:.2f}  edge "
                f"{edge.get('net_excess_bp', 0):+.0f}bp "
                f"vs {edge.get('baseline', '-')}")
        except Exception:
            pass

    def _busy(self, busy, auto=False):
        self.run_button.setEnabled(not busy)
        if auto or not busy:
            self.auto_button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.start()
        else:
            self.progress.stop()

    def run(self):
        self._busy(True)
        self.log.clear()
        fast = self.fast.isChecked()
        names = list(self.presets)
        emit = self.bridge.line.emit

        def one(name):
            if getattr(sys, "frozen", False):
                command = [sys.executable, "--walkforward", "--signal", name]
            else:
                command = [sys.executable, "-u", "walkforward.py",
                           "--signal", name]
            command += ["--final"]
            if fast:
                command += ["--fast", "--max-context", "1000"]
            environment = dict(os.environ, PYTHONUNBUFFERED="1")
            process = subprocess.Popen(
                command, cwd=RUN_DIR, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lines = []
            for line in process.stdout:
                lines.append(line)
                emit(line)
            process.wait()
            return lines

        def work():
            results = {}
            for index, name in enumerate(names, start=1):
                emit("\n===== " + name + f"  ({index} of "
                     f"{len(names)}) =====\n")
                results[name] = one(name)
            return results

        self.app.worker.submit(work, self._finished, self._failed)

    def _append(self, line):
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)
        self.log.insertPlainText(line)
        self.log.ensureCursorVisible()

    def _finished(self, results):
        self._busy(False)
        series, means, labels = [], {}, []
        for name, lines in results.items():
            years, values = self._parse(lines)
            if not values:
                continue
            shown = name.replace("gbm", "GBM ")
            series.append((shown, values))
            means[shown] = sum(values) / len(values)
            if len(years) > len(labels):
                labels = years
        if not series:
            self.scores.setText("no results")
            return
        best = max(means, key=means.get)
        self.chart.lines(series, labels, best=best, baseline=0.0)
        ranked = "   ".join(f"{n} {means[n]:+.0f}"
                            for n in sorted(means, key=means.get,
                                            reverse=True))
        self.scores.setText(f"best {best}    {ranked}")

    @staticmethod
    def _parse(lines):
        years, values = [], []
        for line in lines:
            parts = line.split()
            if len(parts) >= 6 and parts[0].isdigit() and len(parts[0]) == 4:
                excess = [t for t in parts if RE_BP.match(t)]
                if excess:
                    years.append(parts[0][2:])
                    values.append(float(excess[0]))
        return years, values

    def auto(self):
        import automodel

        self._busy(True, auto=True)
        self.use_button.setEnabled(False)
        self.log.clear()
        self.scores.setText(
            "Auto model: testing every model, 3 seeds each...")
        emit = self.bridge.line.emit

        def work():
            return automodel.rank(cwd=RUN_DIR, on_line=emit)

        self.app.worker.submit(work, self._auto_done, self._failed)

    def _auto_done(self, report):
        import automodel
        import config

        self._busy(False)
        self.report = report
        self._append("\n" + automodel.summary(report) + "\n")
        best = report.get("best")
        live = getattr(config, "PRODUCTION_SIGNAL", None)
        row = next((r for r in report["rows"] if r["signal"] == best), None)
        if not row:
            self.scores.setText("Auto model: no model passed")
            return
        self.scores.setText(
            f"best {best}  {row['bp']:+.0f}bp avg, worst seed "
            f"{row['worst']:+.0f}    live: {live}")
        self.use_button.setEnabled(best != live)

    def use_best(self):
        if not self.use_button.isEnabled() or not self.report:
            return
        import config
        import theme as theme_module

        name = self.report["best"]
        live = getattr(config, "PRODUCTION_SIGNAL", None)
        if not confirm(
                self, "Switch the live model?",
                f"The Book, Signals and Orders pages will trade {name} "
                f"instead of {live}, and it will be refitted now.\n\n"
                f"Someone trades from these picks. Continue?",
                danger=True, default_yes=False):
            self.scores.setText("Switch cancelled - live model unchanged.")
            return
        self.use_button.setEnabled(False)
        theme_module.save_signal(name)
        config.PRODUCTION_SIGNAL = name
        self.scores.setText(f"Switching live model to {name}, fitting...")

        def work():
            import production as production_module

            signal, _panel = production_module.fit(name, quiet=True)
            production_module.save(signal, name)
            return name

        self.app.worker.submit(
            work, lambda n: self.scores.setText(
                f"Live model is now {n} (refitted)."), self._failed)

    def _failed(self, error):
        self._busy(False)
        self._append(f"\nfailed: {error}\n")


class CostsPage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        note = CaptionLabel(COST_NOTE, self)
        note.setWordWrap(True)
        root.addWidget(note)

        controls = TitledCard("Position",
                              "NSE cash equity, retail discount broker", self)
        line = QHBoxLayout()
        self.readout = SubtitleLabel("", controls)
        line.addWidget(self.readout)
        self.sub = CaptionLabel("", controls)
        line.addWidget(self.sub)
        line.addStretch(1)
        controls.body.addLayout(line)
        picker = QHBoxLayout()
        picker.setSpacing(14)
        self.delivery = RadioButton("delivery", controls)
        self.delivery.setChecked(True)
        self.intraday = RadioButton("intraday", controls)
        picker.addWidget(self.delivery)
        picker.addWidget(self.intraday)
        picker.addSpacing(20)
        picker.addWidget(CaptionLabel("slippage bp", controls))
        self.slippage = EditSpinBox(controls)
        self.slippage.setRange(0, 100)
        self.slippage.setSingleStep(1)
        self.slippage.setValue(5)
        self.slippage.setFixedWidth(130)
        picker.addWidget(self.slippage)
        picker.addStretch(1)
        controls.body.addLayout(picker)
        self.scale = Slider(Qt.Horizontal, controls)
        self.scale.setRange(2000, 1000000)
        self.scale.setValue(83333)
        controls.body.addWidget(self.scale)
        root.addWidget(controls)

        panes = QHBoxLayout()
        panes.setSpacing(10)
        breakdown = TitledCard("Where the money goes", parent=self)
        self.table = DataTable(
            (("Charge", 130, LEFT), ("Buy", 100, RIGHT),
             ("Sell", 100, RIGHT)), "No charges to show", stretch=0,
            parent=breakdown)
        breakdown.body.addWidget(self.table, 1)
        panes.addWidget(breakdown, 1)
        curve = TitledCard("Cost by position size",
                           "fixed charges do not scale down", self)
        self.chart = Chart(curve, height=250)
        curve.body.addWidget(self.chart, 1)
        panes.addWidget(curve, 1)
        root.addLayout(panes, 1)

        self.delivery.toggled.connect(lambda _c: self.refresh())
        self.slippage.valueChanged.connect(lambda _v: self.refresh())
        self.scale.valueChanged.connect(lambda _v: self.refresh())
        self.refresh()

    def refresh(self):
        import costs as costs_module

        value = max(self.scale.value(), 1000)
        segment = "delivery" if self.delivery.isChecked() else "intraday"
        slippage = float(self.slippage.value())
        trip = costs_module.round_trip(value, segment=segment,
                                       slippage_bp=slippage)
        self.readout.setText(f"{trip.basis_points:.1f} bp")
        self.sub.setText(f"on Rs {value:,.0f}  =  Rs {trip.total:,.0f}  =  "
                         f"{trip.basis_points / 100:.2f}% to break even")
        rows = []
        for (name, buy), (_, sell) in zip(trip.buy.items(),
                                          trip.sell.items()):
            if buy or sell:
                rows.append((name, f"{buy:,.2f}", f"{sell:,.2f}"))
        rows.append(("TOTAL", f"{trip.buy.total:,.2f}",
                     f"{trip.sell.total:,.2f}"))
        self.table.set_rows(rows)
        sizes = [5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000]
        series = [costs_module.cost_bp(s, segment, slippage) for s in sizes]
        self.chart.line(series, labels=["5k", "10L"], fill=True,
                        formatter=lambda v: f"{v:,.0f}bp")


class BacktestSection(TabbedContainer):
    def __init__(self, app, parent=None):
        super().__init__(
            "Backtest",
            (("Backtest", BacktestPage(app)), ("Costs", CostsPage(app))),
            parent)
