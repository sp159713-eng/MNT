from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import config
from theme import Button, Card, Palette, fonts

FIELDS = (("hidden", "width"), ("layers", "layers"), ("dropout", "dropout"),
          ("learning_rate", "learn rate"), ("epochs", "epochs"),
          ("patience", "patience"), ("weight_decay", "weight decay"),
          ("activation", "activation"), ("loss", "loss"),
          ("minutes", "max minutes"))
LIMITS = {"hidden": (4, 2048), "layers": (1, 4), "dropout": (0.0, 0.9),
          "learning_rate": (1e-5, 1e-1), "epochs": (1, 5000), "minutes": (0, 600),
          "patience": (1, 200), "weight_decay": (0.0, 1e-1)}


def parameter_count(values: dict, inputs: int | None = None) -> int:
    import features as features_module

    width = inputs or len(features_module.MODEL_COLUMNS)
    total = 0
    for depth in range(int(values["layers"])):
        size = max(4, int(values["hidden"]) // (2 ** depth))
        total += width * size + size + 2 * size
        width = size
    return total + width + 1


class NNCard(Card):
    def __init__(self, parent):
        super().__init__(parent, "Neural net settings",
                         "used by every nn fit, backtest and Auto model run")
        self.f = fonts()
        self.vars = {}
        grid = tk.Frame(self.body, bg=Palette.panel)
        grid.pack(fill="x")
        for column in range(6):
            grid.grid_columnconfigure(column, weight=1, uniform="nn")
        for index, (key, label) in enumerate(FIELDS):
            row, column = divmod(index, 3)
            tk.Label(grid, text=label, bg=Palette.panel, fg=Palette.muted,
                     font=self.f["small"], anchor="w").grid(
                row=row, column=column * 2, sticky="w", pady=3)
            var = tk.StringVar()
            if key in config.NN_CHOICES:
                widget = ttk.Combobox(grid, textvariable=var, state="readonly",
                                      values=config.NN_CHOICES[key], width=9,
                                      font=self.f["mono_small"])
            else:
                widget = tk.Entry(grid, textvariable=var, width=10,
                                  bg=Palette.panel_high, fg=Palette.text,
                                  insertbackground=Palette.text, relief="flat",
                                  font=self.f["mono_small"])
            widget.grid(row=row, column=column * 2 + 1, sticky="w", pady=3)
            var.trace_add("write", lambda *_: self._count())
            self.vars[key] = var

        footer = tk.Frame(self.body, bg=Palette.panel)
        footer.pack(fill="x", pady=(10, 0))
        self.note = tk.Label(footer, text="", bg=Palette.panel, fg=Palette.muted,
                             font=self.f["small"], anchor="w")
        self.note.pack(side="left", fill="x", expand=True)
        Button(footer, "Save", self.save).pack(side="right")
        Button(footer, "Defaults", self.defaults, kind="ghost").pack(
            side="right", padx=(0, 8))
        self._fill(config.nn_settings())

    def _fill(self, values: dict) -> None:
        for key, var in self.vars.items():
            var.set(str(values[key]))

    def _read(self):
        values, errors = {}, []
        for key, default in config.NN_DEFAULTS.items():
            text = self.vars[key].get().strip()
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

    def _count(self) -> None:
        values, errors = self._read()
        if errors:
            self.note.config(text=f"check: {', '.join(errors)}", fg=Palette.warn)
            return
        self.note.config(text=f"{parameter_count(values):,} parameters",
                         fg=Palette.muted)

    def defaults(self) -> None:
        self._fill(config.NN_DEFAULTS)

    def save(self) -> None:
        import theme as theme_module

        values, errors = self._read()
        if errors:
            self.note.config(text=f"not saved, check: {', '.join(errors)}",
                             fg=Palette.bad)
            return
        theme_module.save_nn(values)
        self.note.config(text=f"saved - {parameter_count(values):,} parameters",
                         fg=Palette.good)
