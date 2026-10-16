from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
from qfluentwidgets import (BodyLabel, CaptionLabel, PillPushButton,
                            TitleLabel, isDarkTheme, qconfig)
from qframelesswindow import FramelessWindow, StandardTitleBar

from qtgui.chart import Chart
from qtgui.common import TitledCard, set_tone

RANGES = (("1D", "groww", None), ("1W", "groww", None),
          ("1M", "groww", None), ("6M", "1d", 126), ("1Y", "1d", 252),
          ("5Y", "1d", 1260), ("MAX", "1d", None))

_SCORES = None


class StockDetail(FramelessWindow):
    RANGES = RANGES

    def __init__(self, app, symbol):
        super().__init__()
        self.app = app
        self.symbol = symbol
        self.closes = []
        self.dates = []
        self.daily_closes = []
        self.daily_dates = []
        self.intraday = {}
        self.active_range = "1Y"
        self.colour = None
        self.setWindowTitle(f"{symbol} - MNT")
        self.setTitleBar(StandardTitleBar(self))
        self.titleBar.setTitle(f"{symbol}  ·  MNT")
        self.resize(900, 660)
        self.setMinimumSize(640, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 48, 24, 20)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.addWidget(TitleLabel(symbol, self))
        self.sector = BodyLabel("", self)
        set_tone(self.sector, "muted")
        head.addSpacing(12)
        head.addWidget(self.sector)
        head.addStretch(1)
        root.addLayout(head)

        self.stats = BodyLabel("Reading cached history...", self)
        mono = QFont("Consolas")
        mono.setPointSizeF(9.5)
        self.stats.setFont(mono)
        self.stats.setWordWrap(True)
        set_tone(self.stats, "muted")
        root.addSpacing(12)
        root.addWidget(self.stats)

        ranges = QHBoxLayout()
        ranges.setSpacing(6)
        self.range_buttons = {}
        for label, _interval, _span in self.RANGES:
            button = PillPushButton(label, self)
            button.clicked.connect(
                lambda _c=False, name=label: self.set_range(name))
            ranges.addWidget(button)
            self.range_buttons[label] = button
        self.range_note = BodyLabel("", self)
        self.range_note.setFont(mono)
        ranges.addSpacing(8)
        ranges.addWidget(self.range_note)
        ranges.addStretch(1)
        root.addSpacing(10)
        root.addLayout(ranges)

        card = TitledCard("Price", "1D-1M live from Groww, longer ranges "
                          "from the local cache", self)
        self.chart = Chart(card, height=320)
        card.body.addWidget(self.chart, 1)
        root.addSpacing(14)
        root.addWidget(card, 1)

        self.model = BodyLabel("Model view: reading the model...", self)
        self.model.setWordWrap(True)
        set_tone(self.model, "muted")
        root.addSpacing(12)
        root.addWidget(self.model)

        qconfig.themeChanged.connect(lambda *_: self.update())
        self.app.worker.submit(self._work, self._done, self._failed)
        self.app.worker.submit(self._score_work, self._score_done,
                               self._score_failed)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(32, 32, 32) if isDarkTheme()
                         else QColor(243, 243, 243))
        painter.end()

    def _work(self):
        import config as config_module
        import data as data_module

        frame = data_module.fetch(self.symbol, interval="1d", quiet=True)
        series = frame["close"].dropna()
        turnover = 0.0
        if "volume" in frame:
            recent = (frame["close"] * frame["volume"]).dropna().tail(20)
            if len(recent):
                turnover = float(recent.mean())
        position = {}
        try:
            import broker as broker_module

            position = broker_module.broker().holdings().get(self.symbol) or {}
        except BaseException:
            position = {}
        return {
            "closes": [float(v) for v in series.tolist()],
            "dates": [str(t)[:10] for t in series.index],
            "sector": config_module.SECTORS.get(self.symbol) or "-",
            "turnover": turnover,
            "position": position,
        }

    def _done(self, payload):
        closes = payload["closes"]
        self.daily_closes = list(closes)
        self.daily_dates = list(payload["dates"])
        self.closes = closes[-1000:]
        self.dates = payload["dates"][-1000:]
        self.sector.setText(payload["sector"])
        if not closes:
            self.stats.setText("No cached price history for this name yet.")
            return
        window = closes[-252:]
        position = payload["position"]
        quantity = position.get("qty", 0) or 0
        average = float(position.get("avg", 0.0) or 0.0)
        money = (f"{quantity:,} @ {average:,.2f} = {quantity * average:,.0f}"
                 if quantity else "nothing held")
        turnover = payload["turnover"]
        self.stats.setText(
            f"Last {closes[-1]:,.2f}     "
            f"52w {min(window):,.2f} - {max(window):,.2f}     "
            f"History {len(closes) / 252.0:,.1f}y     "
            f"Turnover {turnover / 1e7:,.1f} Cr     "
            f"Your money: {money}")
        self.set_range(self.active_range)

    def _failed(self, error):
        self.stats.setText(f"{type(error).__name__}: {error}")
        set_tone(self.stats, "bad")

    def set_range(self, label):
        spec = {name: (interval, span)
                for name, interval, span in self.RANGES}.get(label)
        if spec is None:
            return
        self.active_range = label
        for name, button in self.range_buttons.items():
            button.setChecked(name == label)

        interval, span = spec
        if interval != "groww":
            if not self.daily_closes:
                return
            closes = (self.daily_closes if span is None
                      else self.daily_closes[-span:])
            dates = (self.daily_dates if span is None
                     else self.daily_dates[-span:])
            self._apply(closes, dates)
            return

        cached = self.intraday.get(label)
        if cached is not None:
            self._apply(*cached)
            return
        self.range_note.setText(f"loading {label}...")
        set_tone(self.range_note, "muted")
        self.app.worker.submit(
            lambda: self._intraday_work(label, interval, span),
            self._intraday_done, self._intraday_failed)

    def _intraday_work(self, label, interval, days):
        import groww as groww_module

        closes, labels = groww_module.series(self.symbol, label)
        return (label, closes, labels)

    def _intraday_done(self, payload):
        label, closes, dates = payload
        if len(closes) < 2:
            self.range_note.setText(
                f"{label}: the vendor has no intraday bars for this name")
            set_tone(self.range_note, "warn")
            return
        self.intraday[label] = (closes, dates)
        if self.active_range == label:
            self._apply(closes, dates)

    def _intraday_failed(self, error):
        self.range_note.setText(f"{self.active_range}: {error}")
        set_tone(self.range_note, "bad")

    def _apply(self, closes, dates):
        self.closes = list(closes)
        self.dates = list(dates)
        if len(self.closes) < 2:
            self.range_note.setText(
                f"{self.active_range}: not enough history")
            set_tone(self.range_note, "warn")
            return
        first, last = self.closes[0], self.closes[-1]
        change = ((last / first) - 1.0) * 100.0 if first else 0.0
        self.colour = "good" if change >= 0 else "bad"
        self.range_note.setText(
            f"{last:,.2f}   {change:+.2f}%   {len(self.closes)} points")
        set_tone(self.range_note, self.colour)
        self._draw()

    def _draw(self):
        if len(self.closes) < 2:
            return
        self.chart.line(self.closes, labels=self.dates, colour=self.colour,
                        fill=False, formatter=lambda v: f"{v:,.0f}")

    def _score_work(self):
        global _SCORES
        if _SCORES is not None:
            return _SCORES
        import features as features_module
        import production as production_module

        panel = features_module.cross_sectionalize(
            features_module.build_panel(), require_target=False)
        frame = production_module.scored(panel)
        _SCORES = {str(row.symbol): float(row.score)
                   for row in frame.itertuples()}
        return _SCORES

    def _score_done(self, scores):
        score = scores.get(self.symbol)
        if score is None:
            self.model.setText(
                "Model view: this name is not in the fitted universe, so "
                "the model has no opinion on it.")
            set_tone(self.model, "muted")
            return
        rising = score > 0
        self.colour = "good" if rising else "bad"
        self.model.setText(
            f"Model view: {'UP' if rising else 'DOWN'} "
            f"(score {score:+.4f}) - the line is drawn in "
            f"{'green' if rising else 'red'} to match.")
        set_tone(self.model, self.colour)
        self._draw()

    def _score_failed(self, error):
        self.model.setText(
            f"Model view unavailable: {type(error).__name__}: {error}")
        set_tone(self.model, "muted")
