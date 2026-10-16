import os
import queue
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout,
                               QVBoxLayout, QWidget)
from qfluentwidgets import (CaptionLabel, CheckBox, ComboBox, LineEdit,
                            PrimaryPushButton, PushButton)

import config
import gpujob
import theme
from nncard import parameter_count
from qtgui.common import DataTable, TitledCard, clear_tone, set_tone
from qtgui.gpu_runner import GPURunner

COLUMNS = (("Run", 150, Qt.AlignLeft), ("BP per seed", 220, Qt.AlignLeft),
           ("Avg", 80, Qt.AlignRight), ("Worst", 90, Qt.AlignRight),
           ("IC gap", 110, Qt.AlignRight))


class GPUCard(GPURunner, TitledCard):
    def __init__(self, app=None, parent=None):
        TitledCard.__init__(
            self, "Train on GPU (Kaggle)",
            "free GPU session: walk-forward runs in parallel, results pulled "
            "back here", parent)
        self.app = app
        self.inbox = queue.Queue()
        self.stop_flag = threading.Event()
        self.poller = None
        self.job = None
        self.clamped = False
        self.fields = {}
        self.ticks = {}
        self.inputs = []
        self.preset_boxes = {}
        self.ready, self.why = gpujob.kaggle_ready()

        top = QHBoxLayout()
        self.status = CaptionLabel("", self)
        self.status.setWordWrap(True)
        top.addWidget(self.status, 1)
        self.toggle = PushButton("Show", self)
        self.toggle.clicked.connect(lambda _c=False: self._toggle())
        top.addWidget(self.toggle)
        self.body.addLayout(top)

        self.content = QWidget(self)
        content = QVBoxLayout(self.content)
        content.setContentsMargins(0, 8, 0, 0)
        content.setSpacing(6)
        self.opened = False
        saved = theme.preferences().get("gpu_form") or {}
        form = {"presets": ["default"], "seeds": "3", "data": "India + US",
                "top": "1000", "hours": "8", "delivery": 1, "reference": 1,
                "workspace": gpujob.DEFAULT_WORKSPACE, **saved}
        form["top"] = str(gpujob.clamp_top(form["top"]))
        self.chosen = set(form["presets"])

        row = QHBoxLayout()
        row.addWidget(CaptionLabel("NN presets", self.content))
        self.preset_host = QWidget(self.content)
        self.preset_grid = QGridLayout(self.preset_host)
        self.preset_grid.setContentsMargins(0, 0, 0, 0)
        self.preset_grid.setHorizontalSpacing(14)
        row.addWidget(self.preset_host, 1)
        content.addLayout(row)
        self._build_presets()

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        for column in range(6):
            grid.setColumnStretch(column, 1)
        names = (("seeds", "seeds (1-5)"), ("hours", "hours (1-11.5)"),
                 ("data", "data"), ("top", "US top-N"),
                 ("workspace", "workspace"))
        for index, (key, label) in enumerate(names):
            r, column = divmod(index, 3)
            grid.addWidget(CaptionLabel(label, self.content), r, column * 2)
            if key == "data":
                widget = ComboBox(self.content)
                widget.addItems(["India only", "India + US"])
                widget.setCurrentText(str(form[key]))
                widget.currentTextChanged.connect(self._recount)
            else:
                widget = LineEdit(self.content)
                widget.setText(str(form[key]))
                widget.textChanged.connect(self._recount)
            widget.setMinimumWidth(90)
            grid.addWidget(widget, r, column * 2 + 1)
            self.fields[key] = widget
            self.inputs.append(widget)
        content.addLayout(grid)

        ticks = QHBoxLayout()
        ticks.setSpacing(16)
        for key, label in (("delivery", "delivery features"),
                           ("reference", "include reference blend")):
            box = CheckBox(label, self.content)
            box.setChecked(bool(int(form[key])))
            box.stateChanged.connect(self._recount)
            ticks.addWidget(box)
            self.ticks[key] = box
            self.inputs.append(box)
        ticks.addStretch(1)
        content.addLayout(ticks)

        self.count_note = CaptionLabel("", self.content)
        self.count_note.setWordWrap(True)
        content.addWidget(self.count_note)
        self.estimate_note = CaptionLabel("", self.content)
        self.estimate_note.setWordWrap(True)
        content.addWidget(self.estimate_note)

        buttons = QHBoxLayout()
        self.start_button = PrimaryPushButton("Start on Kaggle", self.content)
        self.start_button.clicked.connect(self.start)
        buttons.addWidget(self.start_button)
        self.forget_button = PushButton("Forget job", self.content)
        self.forget_button.clicked.connect(self.forget)
        buttons.addWidget(self.forget_button)
        if gpujob.dry_run():
            load = PushButton("Load results", self.content)
            load.clicked.connect(self.load_dialog)
            buttons.addWidget(load)
            dry = CaptionLabel("DRY RUN: nothing is sent to Kaggle",
                               self.content)
            set_tone(dry, "warn")
            buttons.addWidget(dry)
        buttons.addStretch(1)
        content.addLayout(buttons)

        self.table = DataTable(COLUMNS, "No results yet", stretch=1,
                               parent=self.content)
        self.table.setMinimumHeight(170)
        self.table.table.setWordWrap(False)
        content.addWidget(self.table)
        self.content.hide()
        self.body.addWidget(self.content)

        if not self.ready:
            self._disable()
        self._recount()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain)
        self.timer.start(400)
        self._resume()
        if self.job:
            self._toggle(True)

    def _tone(self, label, tone):
        if tone is None:
            clear_tone(label)
        else:
            set_tone(label, tone)

    def _build_presets(self):
        while self.preset_grid.count():
            item = self.preset_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        names = config.nn_preset_names()
        self.chosen = {n for n in self.chosen if n in names}
        self.preset_boxes = {}
        for index, name in enumerate(names):
            box = CheckBox(name, self.preset_host)
            box.setChecked(name in self.chosen)
            box.stateChanged.connect(self._presets_ticked)
            self.preset_grid.addWidget(box, index // 4, index % 4)
            self.preset_boxes[name] = box
            if not self.ready:
                box.setEnabled(False)
        self.preset_grid.setColumnStretch(4, 1)

    def _presets_ticked(self, *_args):
        self.chosen = {n for n, b in self.preset_boxes.items()
                       if b.isChecked()}
        self._recount()

    def refresh_presets(self):
        self._build_presets()
        self._recount()

    def _toggle(self, force=None):
        self.opened = (not self.opened) if force is None else force
        self.content.setVisible(self.opened)
        self.toggle.setText("Hide" if self.opened else "Show")

    def _disable(self):
        for widget in self.inputs:
            widget.setEnabled(False)
        self.start_button.setEnabled(False)
        self.status.setText(self.why)
        set_tone(self.status, "warn")

    def _text(self, key):
        widget = self.fields[key]
        return widget.currentText() if isinstance(widget, ComboBox) \
            else widget.text()

    def _params(self):
        errors = []
        self.clamped = False
        presets = [(n, config.nn_settings(n))
                   for n in config.nn_preset_names() if n in self.chosen]
        try:
            seeds = int(self._text("seeds"))
            if not 1 <= seeds <= 5:
                raise ValueError
        except ValueError:
            seeds = 0
            errors.append("seeds")
        try:
            hours = float(self._text("hours"))
            if not 1 <= hours <= 11.5:
                raise ValueError
        except ValueError:
            hours = 8.0
            errors.append("hours")
        try:
            top = int(self._text("top"))
            if top < 1:
                raise ValueError
            if top > gpujob.US_TOP_MAX and self._text("data") == "India + US":
                top = gpujob.US_TOP_MAX
                self.fields["top"].blockSignals(True)
                self.fields["top"].setText(str(top))
                self.fields["top"].blockSignals(False)
                self.clamped = True
        except ValueError:
            top = 1000
            errors.append("US top-N")
        if not self._text("workspace").strip():
            errors.append("workspace")
        params = {"presets": presets, "seeds": seeds, "hours": hours,
                  "top": top, "us": self._text("data") == "India + US",
                  "delivery": self.ticks["delivery"].isChecked(),
                  "reference": self.ticks["reference"].isChecked(),
                  "workspace": self._text("workspace").strip()}
        if not params["presets"] and not params["reference"]:
            errors.append("nothing to run")
        return params, errors

    def _recount(self, *_args):
        params, errors = self._params()
        try:
            counts = ", ".join(
                f"{n}: {parameter_count(v):,}" for n, v in params["presets"])
        except Exception:
            counts = "?"
        self.count_note.setText(
            f"parameters per checked preset - {counts or 'none'}")
        if errors:
            self.estimate_note.setText(f"check: {', '.join(errors)}")
            set_tone(self.estimate_note, "warn")
            return
        groups = gpujob.make_groups(params["presets"], params["us"],
                                    params["top"], params["delivery"],
                                    params["reference"])
        runs, wall = gpujob.estimate(groups, params["seeds"], params["us"])
        _, launch, kill = gpujob.clamp_hours(params["hours"])
        over = wall > launch
        self.estimate_note.setText(
            f"{runs} runs, roughly {wall:.1f} GPU hours at "
            f"{gpujob.PARALLEL} in parallel; no new run starts after "
            f"{launch:.1f} h, anything running is killed at {kill:.1f} h"
            + (" - too long, later runs will be dropped" if over else ""))
        self._tone(self.estimate_note, "warn" if over else "muted")

    def _drain(self):
        try:
            while True:
                kind, *rest = self.inbox.get_nowait()
                if kind == "status":
                    self.status.setText(rest[0])
                    self._tone(self.status, rest[1])
                elif kind == "results":
                    self._show(rest[0])
                elif kind == "busy":
                    self.start_button.setEnabled(self.ready and not rest[0])
        except queue.Empty:
            pass

    def _show(self, rows):
        data = []
        for row in gpujob.summarise(rows):
            seeds = ", ".join(f"{b:+d}" for b in row["bps"]) or "-"
            if row["failed"]:
                seeds += f"  ({row['failed']} failed)"
            data.append((
                row["tag"], seeds,
                "-" if row["avg"] is None else f"{row['avg']:+.0f}",
                "-" if row["worst"] is None else f"{row['worst']:+d}",
                "-" if row["gap"] is None else f"{row['gap']:+.4f}"))
        self.table.set_rows(data)

    def start(self):
        params, errors = self._params()
        if errors:
            self.status.setText(f"not started, check: {', '.join(errors)}")
            set_tone(self.status, "bad")
            return
        if self.job and self.job.get("state") in gpujob.STATES_LIVE:
            self.status.setText("a job is already tracked - forget it first")
            set_tone(self.status, "warn")
            return
        theme.save_key("gpu_form", {
            "presets": sorted(self.chosen), "seeds": self._text("seeds"),
            "hours": self._text("hours"), "data": self._text("data"),
            "top": self._text("top"),
            "delivery": int(self.ticks["delivery"].isChecked()),
            "reference": int(self.ticks["reference"].isChecked()),
            "workspace": params["workspace"]})
        self.stop_flag.clear()
        self._toggle(True)
        self.start_button.setEnabled(False)
        self._show([])
        if self.clamped:
            self._say(f"US top-N lowered to {gpujob.US_TOP_MAX} (larger runs "
                      "out of memory on Kaggle)", "warn")
        settings = dict(config.NN_DEFAULTS)
        threading.Thread(target=self._launch, args=(params, settings),
                         daemon=True).start()

    def load_dialog(self):
        folder = os.environ.get("MNT_GPU_DRY_OUT") or \
            QFileDialog.getExistingDirectory(
                self.window(), "Folder with a results json")
        if folder:
            self._load_folder(folder)

    def forget(self):
        self.stop_flag.set()
        self.job = None
        theme.save_key("gpu_job", None)
        self._show([])
        self.status.setText("job forgotten (a running Kaggle kernel keeps "
                            "going)")
        set_tone(self.status, "muted")
        self.start_button.setEnabled(self.ready)
