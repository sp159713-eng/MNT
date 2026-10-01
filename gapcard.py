from __future__ import annotations

import tkinter as tk

from theme import Card, Palette, fonts

ORACLE = "oracle (perfect)"
DOT = " · "


def latest_walkforward() -> dict | None:
    try:
        import runs

        records = [r for r in runs.load()
                   if isinstance(r, dict) and r.get("kind") == "walkforward"
                   and isinstance(r.get("pooled"), dict)]
    except Exception:
        return None
    if not records:
        return None
    for record in reversed(records):
        if ORACLE in (record.get("baselines") or {}):
            return record
    return records[-1]


def fold_series(record: dict | None):
    years, values = [], []
    try:
        for fold in record.get("folds") or []:
            value = fold["stats"]["net_excess_bp"]
            years.append(str(fold["test_year"])[2:])
            values.append(float(value))
    except Exception:
        return [], []
    return years, values


def _bp(stats) -> float | None:
    try:
        return float(stats["net_excess_bp"])
    except Exception:
        return None


def gap_rows(record: dict) -> list[tuple[str, float, str]]:
    rows = []
    base = record.get("baselines") or {}
    ours = _bp(record.get("pooled"))
    if ours is not None:
        rows.append(("Our bot (pooled)", ours, "ours"))
    for key, label in (("long-horizon (mom_252)", "Long-horizon mom_252"),
                       ("momentum (mom_126)", "Momentum mom_126"),
                       ("reversal (-rev_5)", "Reversal")):
        value = _bp(base.get(key))
        if value is not None:
            rows.append((label, value, "base"))
    value = _bp(base.get(ORACLE))
    if value is not None:
        rows.append(("Perfect bot (impossible ceiling)", value, "oracle"))
    return rows


def readout(record: dict) -> str:
    rows = gap_rows(record)
    ours = next((v for _, v, k in rows if k == "ours"), None)
    oracle = next((v for _, v, k in rows if k == "oracle"), None)
    bases = [v for _, v, k in rows if k == "base"]
    if ours is None:
        return ""
    parts = []
    if oracle:
        parts.append(f"Our bot captures {ours / oracle * 100:.1f}% of the perfect bot "
                     f"({ours:+.0f} of {oracle:+.0f} bp/period)")
    else:
        parts.append(f"Our bot {ours:+.0f} bp/period")
    if bases:
        parts.append(f"beats best baseline by {ours - max(bases):+.0f}bp")
    t_stat = (record.get("pooled") or {}).get("t_stat")
    if t_stat is not None:
        parts.append(f"t={float(t_stat):.2f}")
    return DOT.join(parts)


def caption(record: dict) -> str:
    years = [f.get("test_year") for f in record.get("folds") or []
             if isinstance(f, dict) and f.get("test_year")]
    span = f"{DOT}walk-forward {min(years)}-{max(years)}" if years else f"{DOT}walk-forward"
    return f"run #{record.get('id')}{DOT}{str(record.get('at', ''))[:10]}{span}"


class GapCard(Card):
    EMPTY = "Run a walk-forward in Backtest to see the gap"
    HEIGHT = 150

    def __init__(self, parent):
        super().__init__(parent, "How close to perfect",
                         "net excess return, bp per period")
        self.f = fonts()
        self.rows: list[tuple[str, float, str]] = []
        self.canvas = tk.Canvas(self.body, height=self.HEIGHT, bg=Palette.panel,
                                highlightthickness=0)
        self.canvas.pack(fill="x")
        self.canvas.bind("<Configure>", lambda _e: self._draw())
        self.line = tk.Label(self.body, text="", anchor="w", bg=Palette.panel,
                             fg=Palette.text, font=self.f["small"])
        self.line.pack(fill="x", pady=(6, 0))
        self.note = tk.Label(self.body, text="", anchor="w", bg=Palette.panel,
                             fg=Palette.faint, font=self.f["small"])
        self.note.pack(fill="x")
        self.refresh()

    def refresh(self) -> None:
        record = latest_walkforward()
        self.rows = gap_rows(record) if record else []
        if not self.rows:
            self.line.config(text=self.EMPTY, fg=Palette.muted)
            self.note.config(text="")
            self.canvas.pack_forget()
        else:
            self.canvas.pack(fill="x", before=self.line)
            self.line.config(text=readout(record), fg=Palette.text)
            self.note.config(text=caption(record))
        self._draw()

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        if not self.rows:
            return
        width = max(c.winfo_width(), 300)
        label_w, value_w = 230, 70
        x0, x1 = label_w, width - value_w
        values = [v for _, v, _ in self.rows]
        lo, hi = min(0.0, min(values)), max(0.0, max(values))
        span = (hi - lo) or 1.0
        zero = x0 + (-lo / span) * (x1 - x0)
        step = self.HEIGHT / len(self.rows)
        for index, (label, value, kind) in enumerate(self.rows):
            y = step * (index + 0.5)
            end = zero + value / span * (x1 - x0)
            a, b = min(zero, end), max(zero, end, min(zero, end) + 2)
            if kind == "ours":
                c.create_rectangle(a, y - 9, b, y + 9, fill=Palette.accent, outline="")
            elif kind == "oracle":
                c.create_rectangle(a, y - 9, b, y + 9, fill="", outline=Palette.muted,
                                   dash=(4, 3))
            else:
                c.create_rectangle(a, y - 9, b, y + 9, fill=Palette.panel_high,
                                   outline=Palette.border)
            tone = Palette.text if kind == "ours" else Palette.muted
            c.create_text(label_w - 10, y, text=label, anchor="e", fill=tone,
                          font=self.f["small"])
            c.create_text(b + 8 if value >= 0 else a - 8, y, text=f"{value:+.0f}",
                          anchor="w" if value >= 0 else "e", fill=tone,
                          font=self.f["mono_small"])
        c.create_line(zero, 0, zero, self.HEIGHT, fill=Palette.border)
