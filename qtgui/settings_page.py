import os

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, ComboBox,
                            PrimaryPushButton, PushButton, ScrollArea,
                            SegmentedWidget, isDarkTheme)

from qtgui.common import (TabbedContainer, TitledCard, clear_tone, set_tone,
                          title_row)

MODEL_NOTE = ("Picking a model here only shows its measured results. Use this "
              "model makes it the one the book trades; then refit, because "
              "Book, Orders and Sim refuse a saved fit from a different "
              "model.")
THEME_NOTE = ("Light uses white cards; both themes share the same blue accent. "
              "Switching applies immediately; nothing is cleared.")
STOCKS_NOTE = ("Add, remove and search for stocks on the Stock page. A name in "
               "the UNIVERSE is fitted, ranked and can be bought; a WATCHLIST "
               "name is fetched and shown and never reaches the model. Names "
               "live in artifacts/stocks.json and survive a reinstall; refit "
               "before the Book or Orders tabs are trusted.")
TILES = (("net", "Return, net %/yr"), ("bench", "Benchmark %/yr"),
         ("excess", "Excess vs benchmark"),
         ("edge", "Edge over best simple rule"),
         ("captured", "Of perfect bot captured"),
         ("turnover", "Turnover per period"),
         ("fit", "Training time"), ("pick", "Picks time"))


def note(text, parent):
    label = CaptionLabel(text, parent)
    label.setWordWrap(True)
    return label


def scrolled(owner):
    scroll = ScrollArea(owner)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(ScrollArea.NoFrame)
    scroll.enableTransparentBackground()
    inner = QWidget(scroll)
    inner.setStyleSheet("background:transparent;")
    layout = QVBoxLayout(inner)
    layout.setContentsMargins(0, 0, 12, 0)
    layout.setSpacing(12)
    scroll.setWidget(inner)
    return scroll, layout


class SettingsPage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        import config
        import model_info

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.addLayout(title_row("Settings", self))
        scroll, body = scrolled(self)
        root.addWidget(scroll, 1)

        appearance = TitledCard("Appearance", "applies immediately", self)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(BodyLabel("Theme", self))
        self.theme_pivot = SegmentedWidget(self)
        for name, label in (("dark", "Dark"), ("light", "Light")):
            self.theme_pivot.addItem(
                routeKey=name, text=label,
                onClick=lambda _=False, n=name: self.set_theme(n))
        row.addWidget(self.theme_pivot)
        row.addStretch(1)
        appearance.body.addLayout(row)
        appearance.body.addWidget(note(THEME_NOTE, self))
        body.addWidget(appearance)

        card = TitledCard("Model", "what the book actually trades", self)
        self.model_options = model_info.options(
            getattr(config, "GBM_PRESETS", ()))
        self.model_names = {label: name for name, label in self.model_options}
        live = getattr(config, "PRODUCTION_SIGNAL", "lightgbm")
        self.preview = live
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(BodyLabel("Model", self))
        self.model_box = ComboBox(self)
        self.model_box.addItems([label for _n, label in self.model_options])
        self.model_box.setMinimumWidth(300)
        self.model_box.setCurrentText(model_info.label(live))
        self.model_box.currentTextChanged.connect(self._previewed)
        row.addWidget(self.model_box)
        row.addStretch(1)
        self.use_button = PrimaryPushButton("Use this model", self)
        self.use_button.clicked.connect(lambda: self.set_signal(self.preview))
        row.addWidget(self.use_button)
        self.refit_button = PushButton("Refit now", self)
        self.refit_button.clicked.connect(self.refit)
        row.addWidget(self.refit_button)
        card.body.addLayout(row)
        self.signal_note = note("", self)
        card.body.addWidget(self.signal_note)

        self.tiles = {}
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, (key, title) in enumerate(TILES):
            grid.setColumnStretch(index % 4, 1)
            cell = CardWidget(self)
            inner = QVBoxLayout(cell)
            inner.setContentsMargins(12, 8, 12, 8)
            inner.setSpacing(2)
            value = BodyLabel("-", cell)
            inner.addWidget(value)
            inner.addWidget(CaptionLabel(title, cell))
            grid.addWidget(cell, index // 4, index % 4)
            self.tiles[key] = value
        card.body.addLayout(grid)
        self.stat_note = note("", self)
        card.body.addWidget(self.stat_note)
        card.body.addWidget(note(MODEL_NOTE, self))
        body.addWidget(card)

        stocks = TitledCard("Stocks", "managed on the Stock page", self)
        stocks.body.addWidget(note(STOCKS_NOTE, self))
        body.addWidget(stocks)
        body.addStretch(1)
        self._mark_theme()
        self._mark_signal()

    def _stored_signal(self):
        try:
            import joblib

            import production as production_module

            if not os.path.exists(production_module.PATH):
                return "none"
            return joblib.load(production_module.PATH).get("name") or "unknown"
        except Exception:
            return "unreadable"

    def _previewed(self, text):
        name = self.model_names.get(text)
        if name:
            self.preview = name
            self._mark_signal()

    def _load_stats(self):
        name = self.preview

        def work():
            import model_info

            return model_info.stats(name)

        def done(result):
            if result.get("signal") == self.preview:
                self._show_stats(result)

        def failed(_error):
            self._show_stats({"signal": name, "record": None, "timing": {}})

        self.app.worker.submit(work, done, failed)

    def _text(self, key, text, tone=None):
        tile = self.tiles[key]
        tile.setText(text)
        if tone:
            set_tone(tile, tone)
        else:
            clear_tone(tile)

    def _show_stats(self, result):
        import model_timings

        for key in self.tiles:
            self._text(key, "-")
        record = result.get("record")
        timing = result.get("timing") or {}
        for key in ("fit", "pick"):
            if f"{key}_s" in timing:
                self._text(key, model_timings.human(timing[f"{key}_s"]))
            else:
                self._text(key, "not measured yet")
        if not record:
            self.stat_note.setText(
                "No test results yet for this model. Timing appears after a "
                "refit (training) and Compute holdings (picks).")
            set_tone(self.stat_note, "muted")
            return

        def pct(value):
            return "n/a" if value is None else f"{value:.1f}%"

        def tone(value):
            if value is None:
                return None
            return "good" if value >= 0 else "bad"

        self._text("net", pct(record["net"]))
        self._text("bench", pct(record["bench"]))
        excess = record["excess"]
        self._text("excess", "n/a" if excess is None else f"{excess:+.1f} pts",
                   tone(excess))
        edge = record["edge_bp"]
        self._text("edge", "n/a" if edge is None else f"{edge:+.0f} bp",
                   tone(edge))
        self._text("captured", "not measured" if record["captured"] is None
                   else f"{record['captured']:.1f}%")
        turnover = record["turnover"]
        self._text("turnover",
                   "n/a" if turnover is None else f"{turnover * 100:.0f}%")
        text = (f"Measured on {record['date']}, {record['folds']} "
                f"walk-forward folds (run #{record['run']}).")
        if record["captured_from"]:
            text += (f" Perfect-bot share is from the "
                     f"{record['captured_from']} run.")
        elif record["captured"] is None:
            text += " That run did not include the perfect-bot comparison."
        if record["edge_vs"]:
            text += f" Edge is against {record['edge_vs']}."
        self.stat_note.setText(text)
        set_tone(self.stat_note, "muted")

    def _mark_signal(self):
        import config
        import model_info

        current = getattr(config, "PRODUCTION_SIGNAL", "lightgbm")
        stored = self._stored_signal()
        previewing = self.preview != current
        self.use_button.setEnabled(previewing)
        if previewing:
            self.signal_note.setText(
                f"Previewing {model_info.label(self.preview)}. The book still "
                f"trades {model_info.label(current)}; press Use this model to "
                f"switch.")
            set_tone(self.signal_note, "muted")
        elif stored == current:
            self.signal_note.setText(
                f"Trading {model_info.label(current)}; the saved fit matches.")
            set_tone(self.signal_note, "good")
        else:
            self.signal_note.setText(
                f"Set to {model_info.label(current)}, but the saved fit is "
                f"{model_info.label(stored)}. Book, Orders and Sim will "
                f"refuse to run until this is refitted.")
            set_tone(self.signal_note, "warn")
        self.refit_button.setEnabled(True)
        self._load_stats()

    def set_signal(self, name):
        import config
        import theme as theme_module

        if name == getattr(config, "PRODUCTION_SIGNAL", None):
            return
        theme_module.save_signal(name)
        config.PRODUCTION_SIGNAL = name
        self.preview = name
        self._mark_signal()

    def refit(self):
        import config

        name = getattr(config, "PRODUCTION_SIGNAL", "lightgbm")
        self.refit_button.setEnabled(False)
        self.signal_note.setText(f"Fitting {name}...")
        set_tone(self.signal_note, "muted")

        def work():
            import production as production_module

            signal, _panel = production_module.fit(name, quiet=True)
            production_module.save(signal, name)
            return name

        self.app.worker.submit(work, self._refitted, self._refit_failed)

    def _refitted(self, _name):
        self.refit_button.setEnabled(True)
        self._mark_signal()

    def _refit_failed(self, error):
        self.refit_button.setEnabled(True)
        self.signal_note.setText(str(error)[:200])
        set_tone(self.signal_note, "bad")

    def _mark_theme(self):
        self.theme_pivot.setCurrentItem("dark" if isDarkTheme() else "light")

    def set_theme(self, name):
        if name == ("dark" if isDarkTheme() else "light"):
            return
        self.window().toggle_theme()
        self._mark_theme()

    def on_show(self):
        self._mark_theme()
        self._mark_signal()


class SettingsSection(TabbedContainer):
    def __init__(self, app, parent=None):
        from qtgui.venues_page import VenuePage

        super().__init__(
            "Settings",
            (("General", SettingsPage(app)), ("Venues", VenuePage(app))),
            parent)
