import re

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, ComboBox, LineEdit, PrimaryPushButton, PushButton

import config
from nncard import FIELDS, LIMITS, parameter_count
from qtgui.common import TitledCard, set_tone


class NNCard(TitledCard):
    def __init__(self, parent=None):
        super().__init__("Neural net settings",
                         "used by every nn fit, backtest and Auto model run",
                         parent)
        self.inputs = {}
        self.listeners = []
        self.top = QWidget(self)
        top = QHBoxLayout(self.top)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(CaptionLabel("NN", self.top))
        self.picker = ComboBox(self.top)
        self.picker.setMinimumWidth(220)
        self.picker.addItems(config.nn_preset_names())
        self.picker.setCurrentText("default")
        top.addWidget(self.picker)
        self.new_button = PushButton("New NN", self.top)
        self.new_button.clicked.connect(self._ask)
        top.addWidget(self.new_button)
        self.delete_button = PushButton("Delete", self.top)
        self.delete_button.clicked.connect(self.delete)
        top.addWidget(self.delete_button)
        top.addStretch(1)
        self.body.addWidget(self.top)

        self.ask_row = QWidget(self)
        ask = QHBoxLayout(self.ask_row)
        ask.setContentsMargins(0, 0, 0, 0)
        ask.setSpacing(8)
        ask.addWidget(CaptionLabel("name", self.ask_row))
        self.new_entry = LineEdit(self.ask_row)
        self.new_entry.setMinimumWidth(220)
        self.new_entry.returnPressed.connect(self._create)
        ask.addWidget(self.new_entry)
        ok = PrimaryPushButton("OK", self.ask_row)
        ok.clicked.connect(self._create)
        ask.addWidget(ok)
        cancel = PushButton("Cancel", self.ask_row)
        cancel.clicked.connect(self._cancel)
        ask.addWidget(cancel)
        ask.addStretch(1)
        self.ask_row.hide()
        self.body.addWidget(self.ask_row)

        grid_host = QWidget(self)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        for column in range(6):
            grid.setColumnStretch(column, 1)
        for index, (key, label) in enumerate(FIELDS):
            row, column = divmod(index, 3)
            grid.addWidget(CaptionLabel(label, grid_host), row, column * 2)
            if key in config.NN_CHOICES:
                widget = ComboBox(grid_host)
                widget.addItems(list(config.NN_CHOICES[key]))
                widget.currentTextChanged.connect(self._count)
            else:
                widget = LineEdit(grid_host)
                widget.textChanged.connect(self._count)
            widget.setMinimumWidth(90)
            grid.addWidget(widget, row, column * 2 + 1)
            self.inputs[key] = widget
        self.body.addWidget(grid_host)

        footer = QHBoxLayout()
        self.note = CaptionLabel("", self)
        footer.addWidget(self.note, 1)
        defaults = PushButton("Defaults", self)
        defaults.clicked.connect(self.defaults)
        footer.addWidget(defaults)
        save = PrimaryPushButton("Save", self)
        save.clicked.connect(self.save)
        footer.addWidget(save)
        self.body.addLayout(footer)

        self.picker.currentTextChanged.connect(lambda _t: self._switch())
        self._fill(config.nn_settings(self.picker.currentText()))
        self._buttons()

    def name(self):
        return self.picker.currentText()

    def _text(self, key):
        widget = self.inputs[key]
        return widget.currentText() if isinstance(widget, ComboBox) \
            else widget.text()

    def _say(self, text, tone):
        self.note.setText(text)
        set_tone(self.note, tone)

    def _buttons(self):
        self.delete_button.setEnabled(self.name() != "default")

    def _changed(self):
        for listener in self.listeners:
            listener()

    def _switch(self):
        self._fill(config.nn_settings(self.name()))
        self._buttons()
        self._count()

    def _set_presets(self, current):
        self.picker.blockSignals(True)
        self.picker.clear()
        self.picker.addItems(config.nn_preset_names())
        self.picker.setCurrentText(current)
        self.picker.blockSignals(False)
        self._switch()

    def _ask(self):
        self.new_entry.setText("")
        self.ask_row.show()
        self.new_entry.setFocus()

    def _cancel(self):
        self.ask_row.hide()

    def _create(self):
        text = self.new_entry.text().strip()
        if not text or not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", text):
            self._say("name: 1-24 letters, digits, - or _", "bad")
            return
        if text in config.nn_preset_names():
            self._say(f"'{text}' already exists", "bad")
            return
        import theme as theme_module

        theme_module.save_nn(dict(config.NN_DEFAULTS), text)
        self.ask_row.hide()
        self._set_presets(text)
        self._say(f"created '{text}' from the defaults", "good")
        self._changed()

    def delete(self):
        import theme as theme_module

        name = self.name()
        if name == "default":
            return
        theme_module.delete_nn(name)
        self._set_presets("default")
        self._say(f"deleted '{name}'", "muted")
        self._changed()

    def _fill(self, values):
        for key, widget in self.inputs.items():
            widget.blockSignals(True)
            if isinstance(widget, ComboBox):
                widget.setCurrentText(str(values[key]))
            else:
                widget.setText(str(values[key]))
            widget.blockSignals(False)
        self._count()

    def _read(self):
        values, errors = {}, []
        for key, default in config.NN_DEFAULTS.items():
            text = self._text(key).strip()
            if key in config.NN_CHOICES:
                values[key] = text
                continue
            try:
                value = type(default)(float(text)) if isinstance(default, int) \
                    else float(text)
            except ValueError:
                errors.append(key)
                continue
            low, high = LIMITS[key]
            if not low <= value <= high:
                errors.append(key)
                continue
            values[key] = value
        return values, errors

    def _count(self, *_args):
        values, errors = self._read()
        if errors:
            self._say(f"check: {', '.join(errors)}", "warn")
            return
        self._say(f"{parameter_count(values):,} parameters", "muted")

    def defaults(self):
        self._fill(config.NN_DEFAULTS)

    def save(self):
        import theme as theme_module

        values, errors = self._read()
        if errors:
            self._say(f"not saved, check: {', '.join(errors)}", "bad")
            return
        theme_module.save_nn(values, self.name())
        self._changed()
        self._say(f"saved {self.name()} - {parameter_count(values):,} "
                  "parameters", "good")
