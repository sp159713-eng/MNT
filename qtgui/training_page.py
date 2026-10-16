import importlib.util

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (CaptionLabel, ComboBox, IndeterminateProgressBar,
                            PlainTextEdit, PrimaryPushButton, PushButton,
                            ScrollArea)

from qtgui.common import (DataTable, EditSpinBox, StatCard, TitledCard,
                          clear_tone, confirm, set_tone, title_row)

LEFT, RIGHT = Qt.AlignLeft, Qt.AlignRight
COLUMNS = (("ID", 70, RIGHT), ("When", 150, LEFT), ("Seed", 90, RIGHT),
           ("Names", 90, RIGHT), ("Unseen IC", 110, RIGHT),
           ("Excess/yr", 110, RIGHT), ("Trained IC", 110, RIGHT))
TILES = (("unseen_ic", "rank IC, unseen names"),
         ("unseen_excess", "top-6 excess, unseen"),
         ("same_ic", "rank IC, trained names"),
         ("slots", "runs kept"))
NAN = float("nan")
SLOW_NOTE = ("NN training on this PC's CPU (2-core) is slow - expect a long "
             "run. Use Train on GPU (Kaggle) below for real NN work.")
EMPTY_VERDICT = ("No runs yet. Train fits a random slice of the universe over "
                 "a random window, then scores it on names it never saw. That "
                 "unseen number is the one that matters.")


def _has_unseen(record):
    return (bool(record.get("scored_on_unseen_names", True))
            and bool(record.get("held_out", ["?"])))


class TrainingPage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setObjectName("page-Training")
        self.busy = False
        self.lines = []
        self.shown = 0
        self.nn_card = None
        self.gpu_card = None
        import theme

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(12)
        row = title_row("Training", self)
        self.model_labels = self._model_options()
        saved = theme.preferences().get("train_signal")
        self.model_box = ComboBox(self)
        self.model_box.setMinimumWidth(300)
        self.model_box.addItems(list(self.model_labels))
        self.model_box.setCurrentText(next(
            (label for label, name in self.model_labels.items()
             if name and name == saved), next(iter(self.model_labels))))
        row.addWidget(CaptionLabel("model", self))
        row.addWidget(self.model_box)
        row.addWidget(CaptionLabel("names", self))
        self.names = EditSpinBox(self)
        self.names.setRange(6, 120)
        self.names.setSingleStep(2)
        self.names.setValue(30)
        row.addWidget(self.names)
        self.promote_button = PushButton("Promote", self)
        self.promote_button.clicked.connect(self.promote)
        row.addWidget(self.promote_button)
        self.run_button = PrimaryPushButton("Train", self)
        self.run_button.clicked.connect(self.start)
        row.addWidget(self.run_button)
        root.addLayout(row)

        self.model_note = CaptionLabel(SLOW_NOTE, self)
        self.model_note.setWordWrap(True)
        root.addWidget(self.model_note)
        self.model_box.currentTextChanged.connect(
            lambda _t: self._model_changed())
        self._model_changed(save=False)

        self.progress = IndeterminateProgressBar(self)
        self.progress.hide()
        root.addWidget(self.progress)

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(ScrollArea.NoFrame)
        scroll.enableTransparentBackground()
        inner = QWidget(scroll)
        inner.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 12, 0)
        layout.setSpacing(14)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        if importlib.util.find_spec("torch") is not None:
            from qtgui.gpu_card import GPUCard
            from qtgui.nn_card import NNCard

            self.nn_card = NNCard(inner)
            layout.addWidget(self.nn_card)
            self.gpu_card = GPUCard(app, inner)
            layout.addWidget(self.gpu_card)
            self.nn_card.listeners.append(self._presets_changed)
            self.nn_card.listeners.append(self.gpu_card.refresh_presets)

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.tiles = {}
        for key, label in TILES:
            card = StatCard(label, inner)
            tiles.addWidget(card, 1)
            self.tiles[key] = card.value
        layout.addLayout(tiles)

        panes = QVBoxLayout()
        panes.setSpacing(14)
        right = TitledCard("Runs", "scored on names the fit never saw", inner)
        self.table = DataTable(COLUMNS, "No runs yet", stretch=1, parent=right)
        self.table.setMinimumHeight(280)
        self.table.table.setWordWrap(False)
        right.body.addWidget(self.table, 1)
        self.verdict = CaptionLabel("", right)
        self.verdict.setWordWrap(True)
        right.body.addWidget(self.verdict)
        panes.addWidget(right)
        left = TitledCard("Run log",
                          "a random slice of the universe, a random window",
                          inner)
        self.text = PlainTextEdit(left)
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("Press Train to start a run")
        self.text.setMinimumHeight(220)
        left.body.addWidget(self.text, 1)
        panes.addWidget(left)
        layout.addLayout(panes)
        layout.addStretch(1)

        self.pump_timer = QTimer(self)
        self.pump_timer.setInterval(200)
        self.pump_timer.timeout.connect(self._pump)

    def _model_options(self):
        import config

        options = {f"Live model ({config.PRODUCTION_SIGNAL})": None}
        for name in config.nn_preset_names():
            tag = "nn" if name == "default" else f"nn@{name}"
            options[f"NN: {name}"] = tag
            options[f"NN blend: {name}"] = f"blend:{tag}+mom_252"
        return options

    def _presets_changed(self):
        keep = self._model_name()
        self.model_labels = self._model_options()
        self.model_box.blockSignals(True)
        self.model_box.clear()
        self.model_box.addItems(list(self.model_labels))
        self.model_box.setCurrentText(next(
            (label for label, name in self.model_labels.items()
             if name and name == keep), next(iter(self.model_labels))))
        self.model_box.blockSignals(False)
        self._model_changed()

    def _model_name(self):
        return self.model_labels.get(self.model_box.currentText())

    def _model_changed(self, save=True):
        import theme

        name = self._model_name()
        if save:
            theme.save_key("train_signal", name)
        self.model_note.setVisible(bool(name))

    def on_show(self):
        self.refresh()

    def _say(self, text, tone):
        self.verdict.setText(text)
        if tone is None:
            clear_tone(self.verdict)
        else:
            set_tone(self.verdict, tone)

    def _tile(self, key, text, tone=None):
        self.tiles[key].setText(text)
        if tone is None:
            clear_tone(self.tiles[key])
        else:
            set_tone(self.tiles[key], tone)

    def refresh(self):
        import training as training_module

        records = sorted(training_module.runs(), key=lambda r: r.get("id", 0),
                         reverse=True)
        rows = []
        for record in records:
            unseen = _has_unseen(record)
            rows.append((
                record.get("id", 0), record.get("at", ""),
                record.get("seed", 0), record.get("count", 0),
                (f"{record.get('rank_ic', NAN):+.4f}" if unseen
                 else "none held"),
                (f"{record.get('excess_annual_pct', 0.0):+.2f}%" if unseen
                 else "-"),
                f"{record.get('same_rank_ic', NAN):+.4f}"))
        self.table.set_rows(rows)
        self.tiles["slots"].setText(str(len(records)))
        if records:
            self._show_tiles(records[0])
        else:
            self._say(EMPTY_VERDICT, "muted")

    def _show_tiles(self, record):
        unseen = record.get("rank_ic", NAN)
        excess = record.get("excess_annual_pct", 0.0)
        same = f"{record.get('same_rank_ic', NAN):+.4f}"
        if not _has_unseen(record):
            self._tile("unseen_ic", "none held", "warn")
            self._tile("unseen_excess", "-", "warn")
            self.tiles["same_ic"].setText(same)
            return
        self._tile("unseen_ic", f"{unseen:+.4f}",
                   "good" if unseen > 0 else "bad")
        self._tile("unseen_excess", f"{excess:+.2f}%",
                   "good" if excess > 0 else "bad")
        self.tiles["same_ic"].setText(same)

    def start(self):
        import training as training_module

        if self.busy:
            return
        self.busy = True
        self.lines, self.shown = [], 0
        self.text.clear()
        self.run_button.setEnabled(False)
        self.progress.show()
        self.progress.start()
        self._say("training...", "muted")
        count = int(self.names.value())
        signal_name = self._model_name()
        self.pump_timer.start()
        self.app.worker.submit(
            lambda: training_module.run(
                count, signal_name=signal_name, on_log=self.lines.append),
            self._done, self._failed)

    def _pump(self):
        while self.shown < len(self.lines):
            self.text.appendPlainText(self.lines[self.shown])
            self.shown += 1
        if not self.busy:
            self.pump_timer.stop()

    def _stop(self):
        self.busy = False
        self.run_button.setEnabled(True)
        self.progress.stop()
        self.progress.hide()
        self._pump()

    def _done(self, record):
        self._stop()
        self.refresh()
        self._show_tiles(record)
        unseen = record.get("rank_ic", NAN)
        same = record.get("same_rank_ic", NAN)
        run_id = record.get("id", 0)
        if not _has_unseen(record):
            self._say(f"run {run_id}: trained on the whole roster, so no "
                      "names were held back - this one cannot say anything "
                      "about unseen stocks. Use fewer names.", "warn")
            return
        if unseen > 0 and unseen >= same * 0.5:
            note = ("held up on names it never saw - one run is not "
                    "evidence, run several before promoting any of them")
            tone = "good"
        elif unseen > 0:
            note = ("positive on unseen names but well below its own trained "
                    "names - still learning the roster, not the market")
            tone = "warn"
        else:
            note = ("no edge on unseen names - do not promote, run again for "
                    "a different slice and window")
            tone = "bad"
        self._say(f"run {run_id}: {note}", tone)

    def _failed(self, error):
        self._stop()
        self.lines.append(f"failed: {error}")
        self._pump()
        self._say(f"failed: {error}", "bad")

    def promote(self):
        import training as training_module

        if self.busy:
            return
        selected = self.table.table.selectedItems()
        if not selected:
            self._say("Select a run to promote.", "warn")
            return
        row = selected[0].row()
        run_id = int(self.table.table.item(row, 0).text())
        record = next((r for r in training_module.runs()
                       if r.get("id") == run_id), None)
        unseen = record.get("rank_ic", 0.0) if record else 0.0
        warning = ("" if unseen > 0 else
                   "\n\nThis run showed no edge on names it never saw.")
        if not confirm(
                self, "Promote",
                f"Make training run {run_id} the model that Book, Orders and "
                f"Sim trade?{warning}"):
            return
        self.busy = True
        self.promote_button.setEnabled(False)
        self.app.worker.submit(lambda: training_module.promote(run_id),
                               self._promoted, self._promote_failed)

    def _promoted(self, problem):
        self.busy = False
        self.promote_button.setEnabled(True)
        if problem:
            self._say(problem, "bad")
            return
        self._say("Promoted. Book, Orders and Sim now score with this fit.",
                  "good")

    def _promote_failed(self, error):
        self.busy = False
        self.promote_button.setEnabled(True)
        self._say(f"promote failed: {error}", "bad")
