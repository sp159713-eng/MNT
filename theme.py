"""Colours, fonts and the two custom widgets the GUI is built from.

WHY THIS IS SEPARATE

tkinter's defaults look like 1995 because ttk's native Windows theme refuses
most colour options. The escape is to switch to the 'clam' theme, which honours
them, and then restyle everything from scratch - which is only bearable if the
palette lives in one place. Every colour in the application is named here and
nowhere else.

WHY THE CHARTS ARE HAND-DRAWN

matplotlib is not installed, and adding it to draw four small charts would pull
in a large dependency and a second rendering model. A Canvas can draw a line and
a rectangle perfectly well; what it cannot do is choose sensible axis bounds, so
that part is written out below rather than wished away.
"""

from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import font as tkfont


class Dark:
    """The original scheme, chosen for contrast against long numeric tables."""

    name = "dark"

    bg = "#0d1117"            # window
    panel = "#161b22"         # cards
    panel_high = "#21262d"    # hover / selected
    border = "#30363d"

    text = "#f0f6fc"
    muted = "#8b949e"
    faint = "#484f58"

    accent = "#58a6ff"        # primary actions, focus
    accent_dim = "#1f6feb"
    accent_subtle = "#0d2240"
    on_accent = "#ffffff"     # text that sits ON an accent fill
    good = "#3fb950"          # profit, pass
    bad = "#f85149"           # loss, fail
    warn = "#d29922"          # marginal

    # Row tints for tables that encode a side. Applied as a BACKGROUND band
    # rather than as foreground colour: colouring the text turned every figure
    # in a BUY row green, and green on a number reads as profit, which is not
    # what the column says. A band behind neutral text encodes the side without
    # claiming anything about the money.
    buy_row = "#13251a"
    sell_row = "#261619"
    stripe = "#1b2029"        # zebra banding for tables with no side to encode

    # Surface for log and code panes, which sit INSIDE a card and should read
    # as recessed. Dark uses the window colour, which is darker than a card;
    # light cannot, because the window colour is a mid grey and a grey block
    # inside a white card reads as a hole rather than an inset.
    inset = "#0d1117"

    grid = "#21262d"
    series = ("#4c9aff", "#3fb950", "#d29922", "#bc8cff", "#f85149")

    sidebar_bg = "#010409"
    sidebar_hover = "#161b22"
    sidebar_active = "#1f6feb"


class Light:
    """A light scheme in the manner of Groww: white cards, teal-green accent.

    Inspired by that app rather than copied from it - these are hand-picked
    values that read the same way, not sampled brand assets, and nothing here
    should be taken as Groww's official palette.

    The one substantive departure is `good` and `bad`. Indian broking apps
    render gains green and losses red, and this app follows that on price and
    P&L - but `accent` is ALSO green here, so an accent fill and a profit
    figure would be the same colour and the reader would have to guess which
    meaning applied. `good` is therefore pushed darker than `accent`, far
    enough apart to tell at a glance.
    """

    name = "light"

    bg = "#f4f6f9"            # window
    panel = "#ffffff"         # cards
    panel_high = "#eef1f6"    # hover / selected
    border = "#e2e7ee"

    text = "#14181f"
    muted = "#5f6b7a"
    faint = "#98a2b3"

    accent = "#00b386"        # primary actions
    accent_dim = "#009973"
    accent_subtle = "#e3f7f1"
    on_accent = "#ffffff"
    good = "#00794f"          # darker than accent, deliberately - see above
    bad = "#e0492c"
    warn = "#b06f00"          # darkened for contrast on white

    buy_row = "#effaf6"
    sell_row = "#fdf0ec"
    stripe = "#fafbfd"

    inset = "#f7f9fb"

    grid = "#e6eaf0"
    series = ("#2f6df6", "#00794f", "#b06f00", "#8250df", "#e0492c")

    sidebar_bg = "#ffffff"
    sidebar_hover = "#eef4f2"
    # The 3px indicator stripe, so it has to be saturated. Set to the pale
    # tint at first, which on a white sidebar was invisible - the active page
    # was identifiable only by a faint grey row.
    sidebar_active = "#00b386"


class Palette:
    """The scheme in force. Every colour in the application is read from here.

    Attributes are COPIED onto this class by set_theme rather than the app
    holding a reference to Dark or Light, because every widget reads
    `Palette.bg` at construction time and there is no indirection to hook. That
    also means a running window does not restyle itself - the colours it was
    built with are baked into each widget - so switching themes rebuilds the
    window. See App.apply_theme.
    """


THEMES = {"dark": Dark, "light": Light}


def set_theme(name: str) -> str:
    """Point Palette at one of THEMES. Returns the name actually applied."""
    source = THEMES.get(name, Dark)
    for key, value in vars(source).items():
        if not key.startswith("__"):
            setattr(Palette, key, value)
    return source.name


def _preference_path() -> str:
    import config

    return os.path.join(config.MODEL_DIR, "ui.json")


def preferences() -> dict:
    """Everything remembered about the interface. Never raises - a broken
    preferences file must not stop the window opening."""
    try:
        with open(_preference_path(), encoding="utf-8") as handle:
            saved = json.load(handle)
    except Exception:
        return {}
    return saved if isinstance(saved, dict) else {}


def _write(values: dict) -> None:
    import config

    try:
        os.makedirs(config.MODEL_DIR, exist_ok=True)
        path = _preference_path()
        temporary = f"{path}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(values, handle, indent=2)
        os.replace(temporary, path)
    except OSError:
        # A preference that cannot be written is a preference that does not
        # survive the session. That is a smaller problem than refusing to
        # switch, so it is swallowed.
        pass


def saved_theme() -> str:
    return preferences().get("theme", "dark")


def save_theme(name: str) -> None:
    """Merged into whatever else is remembered, not written over it.

    This used to dump {"theme": name} and nothing else, which was harmless
    while the theme was the only preference. It stopped being harmless the
    moment a second key existed: switching theme would silently drop the
    remembered model, and the app would go back to trading the default without
    saying anything.
    """
    _write(dict(preferences(), theme=name))


def saved_signal(default: str = "lightgbm") -> str:
    """Which model the book should trade, as chosen in Settings."""
    name = preferences().get("signal", default)
    return name if isinstance(name, str) and name else default


def save_signal(name: str) -> None:
    _write(dict(preferences(), signal=name))


set_theme(saved_theme())


def fonts() -> dict:
    """Segoe UI at a few sizes. Tabular figures for anything in a column."""
    return {
        "h1": tkfont.Font(family="Segoe UI Semibold", size=18),
        "h2": tkfont.Font(family="Segoe UI Semibold", size=12),
        "body": tkfont.Font(family="Segoe UI", size=10),
        "small": tkfont.Font(family="Segoe UI", size=9),
        "label": tkfont.Font(family="Segoe UI Semibold", size=9),
        "mono": tkfont.Font(family="Consolas", size=10),
        "mono_small": tkfont.Font(family="Consolas", size=9),
        "number": tkfont.Font(family="Consolas", size=22),
    }


class Card(tk.Frame):
    """A titled panel. The unit the whole layout is composed of."""

    def __init__(self, parent, title: str = "", subtitle: str = "", **kwargs):
        super().__init__(parent, bg=Palette.panel, highlightthickness=1,
                         highlightbackground=Palette.border, **kwargs)
        self.fonts = fonts()
        if title:
            header = tk.Frame(self, bg=Palette.panel)
            header.pack(fill="x", padx=18, pady=(13, 10))
            tk.Label(header, text=title, bg=Palette.panel, fg=Palette.text,
                     font=self.fonts["h2"]).pack(side="left")
            if subtitle:
                tk.Label(header, text=subtitle, bg=Palette.panel,
                         fg=Palette.muted, font=self.fonts["small"]).pack(
                    side="right")
            
            separator = tk.Frame(self, bg=Palette.border, height=1)
            separator.pack(fill="x")
            
        self.body = tk.Frame(self, bg=Palette.panel)
        self.body.pack(fill="both", expand=True, padx=18, pady=(10, 16))


class Chart(tk.Canvas):
    """Line and bar charts on a Canvas.

    Axis bounds are the only genuinely fiddly part. The rule used throughout:
    pad the data range by 8% so points never sit on the frame, and always
    include zero on bar charts, because a bar chart whose baseline is not zero
    misrepresents every comparison drawn from it.
    """

    PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 52, 14, 14, 26

    def __init__(self, parent, height: int = 200, **kwargs):
        super().__init__(parent, bg=Palette.panel, height=height,
                         highlightthickness=0, **kwargs)
        self.fonts = fonts()
        self._render = None
        self.bind("<Configure>", lambda _event: self._redraw())

    def _redraw(self) -> None:
        self.delete("all")
        if self._render:
            self._render()

    def _plot_area(self) -> tuple[float, float, float, float]:
        width = max(self.winfo_width(), 260)
        height = max(self.winfo_height(), 120)
        return (self.PAD_LEFT, self.PAD_TOP,
                width - self.PAD_RIGHT, height - self.PAD_BOTTOM)

    def _frame(self, lo: float, hi: float, labels: int = 4,
               formatter=lambda v: f"{v:,.0f}") -> None:
        """Horizontal gridlines with value labels down the left edge."""
        x0, y0, x1, y1 = self._plot_area()
        for i in range(labels + 1):
            fraction = i / labels
            y = y1 - fraction * (y1 - y0)
            value = lo + fraction * (hi - lo)
            self.create_line(x0, y, x1, y, fill=Palette.grid, dash=(2, 4))
            self.create_text(x0 - 8, y, text=formatter(value), anchor="e",
                             fill=Palette.muted, font=self.fonts["mono_small"])

    def line(self, series: list[float], labels: list[str] | None = None,
             colour: str | None = None, fill: bool = True,
             formatter=lambda v: f"{v:,.0f}", baseline: float | None = None) -> None:
        """A single line, optionally shaded to the bottom of the plot."""

        def render():
            if len(series) < 2:
                self.create_text(self.winfo_width() / 2, self.winfo_height() / 2,
                                 text="not enough data", fill=Palette.muted,
                                 font=self.fonts["small"])
                return

            lo, hi = min(series), max(series)
            if baseline is not None:
                lo, hi = min(lo, baseline), max(hi, baseline)
            span = (hi - lo) or 1.0
            lo, hi = lo - span * 0.08, hi + span * 0.08
            self._frame(lo, hi, formatter=formatter)

            x0, y0, x1, y1 = self._plot_area()
            step = (x1 - x0) / (len(series) - 1)

            def point(index: int, value: float) -> tuple[float, float]:
                return (x0 + index * step,
                        y1 - (value - lo) / (hi - lo) * (y1 - y0))

            points = [point(i, v) for i, v in enumerate(series)]
            stroke = colour or Palette.accent

            if fill:
                polygon = points + [(x1, y1), (x0, y1)]
                self.create_polygon(polygon, fill=Palette.accent_dim,
                                    outline="", stipple="gray12")
            if baseline is not None:
                _, y = point(0, baseline)
                self.create_line(x0, y, x1, y, fill=Palette.muted, dash=(3, 3))

            flat = [coordinate for pair in points for coordinate in pair]
            self.create_line(flat, fill=stroke, width=2.5, smooth=False)

            if labels:
                for index in (0, len(labels) - 1):
                    self.create_text(point(index, series[index])[0], y1 + 13,
                                     text=labels[index], fill=Palette.muted,
                                     font=self.fonts["mono_small"],
                                     anchor="w" if index == 0 else "e")

        self._render = render
        self._redraw()

    def bars(self, values: list[float], labels: list[str],
             formatter=lambda v: f"{v:,.0f}", threshold: float | None = None
             ) -> None:
        """Vertical bars, coloured by sign, with an optional threshold rule."""

        def render():
            if not values:
                return
            lo, hi = min(values + [0.0]), max(values + [0.0])
            if threshold is not None:
                hi = max(hi, threshold)
            span = (hi - lo) or 1.0
            lo, hi = lo - span * 0.08, hi + span * 0.08
            self._frame(lo, hi, formatter=formatter)

            x0, y0, x1, y1 = self._plot_area()
            slot = (x1 - x0) / len(values)
            width = min(slot * 0.62, 34)

            def y_of(value: float) -> float:
                return y1 - (value - lo) / (hi - lo) * (y1 - y0)

            zero = y_of(0.0)
            for index, value in enumerate(values):
                centre = x0 + slot * (index + 0.5)
                colour = Palette.good if value >= 0 else Palette.bad
                y_val = y_of(value)
                top_y = min(y_val, zero) - 1
                bottom_y = max(y_val, zero)
                self.create_rectangle(centre - width / 2, top_y,
                                      centre + width / 2, bottom_y,
                                      fill=colour, outline="")
                self.create_text(centre, y1 + 13, text=labels[index],
                                 fill=Palette.muted,
                                 font=self.fonts["mono_small"])

            self.create_line(x0, zero, x1, zero, fill=Palette.muted)
            if threshold is not None:
                y = y_of(threshold)
                self.create_line(x0, y, x1, y, fill=Palette.warn, dash=(4, 3))
                self.create_text(x1, y - 8, text=f"cost {threshold:.0f}bp",
                                 anchor="e", fill=Palette.warn,
                                 font=self.fonts["mono_small"])

        self._render = render
        self._redraw()


def style_widgets(root: tk.Misc) -> None:
    """Restyle ttk under the 'clam' theme, which is the one that takes colours."""
    from tkinter import ttk

    style = ttk.Style(root)
    style.theme_use("clam")
    body = fonts()["body"]

    # clam draws every border from lightcolor/darkcolor/bordercolor, and its
    # defaults for those are near-white. Setting background alone therefore
    # leaves a bright 1px rectangle around each table and a raised bevel on
    # each heading - the two things that made this look like a Windows 95
    # dialog dropped into a dark theme. They have to be set to the surface
    # colour explicitly; borderwidth=0 does not reach them.
    flat = dict(bordercolor=Palette.panel, lightcolor=Palette.panel,
                darkcolor=Palette.panel)

    style.configure("Treeview",
                    background=Palette.panel, fieldbackground=Palette.panel,
                    foreground=Palette.text, borderwidth=0, relief="flat",
                    rowheight=30, font=fonts()["mono_small"], **flat)
    # Flat, and a shade darker than the rows rather than lighter: a heading is
    # a label for the column, not a raised control to be clicked at.
    style.configure("Treeview.Heading",
                    background=Palette.bg, foreground=Palette.muted,
                    borderwidth=0, relief="flat", padding=(10, 9),
                    font=fonts()["label"],
                    bordercolor=Palette.bg, lightcolor=Palette.bg,
                    darkcolor=Palette.bg)
    style.map("Treeview.Heading",
              background=[("active", Palette.panel_high)],
              foreground=[("active", Palette.text)])
    style.map("Treeview", background=[("selected", Palette.accent_subtle)],
              foreground=[("selected", Palette.text)])

    # Every ttk widget below needs its light/dark/bordercolor pinned for the
    # same reason the Treeview did: clam bevels them near-white regardless of
    # `background`, which is invisible against a dark page and glaring on a
    # light one. Collected here rather than repeated per widget.
    surface = dict(bordercolor=Palette.panel_high,
                   lightcolor=Palette.panel_high, darkcolor=Palette.panel_high)

    style.configure("TCombobox", fieldbackground=Palette.panel_high,
                    background=Palette.panel_high, foreground=Palette.text,
                    arrowcolor=Palette.muted, borderwidth=0, font=body,
                    padding=4, **surface)
    style.map("TCombobox",
              fieldbackground=[("readonly", Palette.panel_high)],
              selectbackground=[("readonly", Palette.panel_high)],
              selectforeground=[("readonly", Palette.text)],
              arrowcolor=[("active", Palette.text)])

    style.configure("TCheckbutton", background=Palette.bg,
                    foreground=Palette.muted, focuscolor=Palette.bg,
                    indicatorcolor=Palette.panel_high, **surface)
    style.map("TCheckbutton",
              background=[("active", Palette.bg)],
              foreground=[("active", Palette.text)],
              indicatorcolor=[("selected", Palette.accent)])
    # The slider is drawn from `background`; the trough from `troughcolor`. Left
    # to itself clam bevels both with its near-white light/dark defaults, which
    # rendered the whole control as a white bar with a striped grip - the single
    # most off-theme widget in the app. light/dark are pinned to the trough so
    # only `background` shows, which puts the accent on the handle alone.
    style.configure("Horizontal.TScale", background=Palette.accent,
                    troughcolor=Palette.bg, borderwidth=0,
                    bordercolor=Palette.border, lightcolor=Palette.bg,
                    darkcolor=Palette.bg)

    # ttk.Spinbox rather than tk.Spinbox at the call sites: tk's arrows are
    # drawn by Windows and ignore buttonbackground, so they stayed as two white
    # nubs however the field was coloured. The ttk widget honours arrowcolor.
    style.configure("TSpinbox", fieldbackground=Palette.panel_high,
                    background=Palette.panel_high, foreground=Palette.text,
                    arrowcolor=Palette.muted, borderwidth=0, arrowsize=12,
                    padding=(6, 4), bordercolor=Palette.panel_high,
                    lightcolor=Palette.panel_high, darkcolor=Palette.panel_high)
    style.map("TSpinbox",
              fieldbackground=[("readonly", Palette.panel_high)],
              arrowcolor=[("active", Palette.text)],
              bordercolor=[("focus", Palette.accent)])
    style.configure("TProgressbar", background=Palette.accent,
                    troughcolor=Palette.bg, borderwidth=0,
                    bordercolor=Palette.border, lightcolor=Palette.bg,
                    darkcolor=Palette.bg)
    # arrowsize=0 removes the stepper buttons entirely. They were rendering as
    # Windows-default 3D arrows at both ends of every scrollbar - the single
    # most dated thing left on screen once the tables were fixed.
    for bar in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        # Thumb on `faint`, not `border`. Border sits a hair off the page
        # colour, which in the light scheme made the scrollbar disappear
        # completely - correct is quiet, not absent, and a control you cannot
        # see is one you cannot grab.
        style.configure(bar, background=Palette.faint,
                        troughcolor=Palette.bg, borderwidth=0, arrowsize=0,
                        arrowcolor=Palette.bg, bordercolor=Palette.bg,
                        lightcolor=Palette.faint, darkcolor=Palette.faint)
        style.map(bar, background=[("active", Palette.muted)])


class Button(tk.Label):
    """A flat button. tk.Button cannot be made to look like this on Windows."""

    def __init__(self, parent, text: str, command=None, kind: str = "primary",
                 **kwargs):
        # Every value read from Palette, none written as a literal. The hover
        # and pressed shades used to be hard-coded dark-theme hexes, which was
        # invisible while there was only one theme and would have left blue
        # GitHub-dark buttons scattered through the light one.
        colours = {
            "primary": (Palette.accent, Palette.on_accent,
                        Palette.accent_dim, Palette.accent_dim),
            "ghost": (Palette.panel_high, Palette.text,
                      Palette.border, Palette.panel),
            "danger": (Palette.bad, Palette.on_accent,
                       Palette.bad, Palette.bad),
        }[kind]
        self.kind = kind
        self.base, self.hover, self.pressed = colours[0], colours[2], colours[3]
        super().__init__(parent, text=text, bg=self.base, fg=colours[1],
                         font=fonts()["body"], padx=18, pady=8,
                         cursor="hand2", **kwargs)
        self.command = command
        self.enabled = True
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda _e: self.enabled and self.config(bg=self.hover))
        self.bind("<Leave>", lambda _e: self.config(bg=self.base))

    def _click(self, _event) -> None:
        if self.enabled and self.command:
            self.config(bg=self.pressed)
            self.update_idletasks()
            self.after(100, lambda: self.config(bg=self.hover) if self.enabled else None)
            self.after(100, self.command)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.config(bg=self.base if enabled else Palette.border,
                    fg=(self.cget("fg") if enabled else Palette.faint),
                    cursor="hand2" if enabled else "arrow")
        if enabled:
            # Restore the foreground the kind was built with rather than
            # assuming white: a ghost button's text is Palette.text, and
            # re-enabling one used to repaint it white on a pale surface.
            self.config(fg=Palette.on_accent if self.kind != "ghost"
                        else Palette.text)


class SidebarButton(tk.Frame):
    """A sidebar nav item with an active indicator stripe."""
    def __init__(self, parent, text, command=None, **kwargs):
        super().__init__(parent, bg=Palette.sidebar_bg, cursor="hand2", **kwargs)
        self.command = command
        self.active = False
        self._fonts = fonts()
        
        # 3px accent stripe on the left edge
        self.stripe = tk.Frame(self, width=3, bg=Palette.sidebar_bg)
        self.stripe.pack(side="left", fill="y")
        
        self.label = tk.Label(self, text=text, bg=Palette.sidebar_bg,
                             fg=Palette.muted, font=self._fonts["body"],
                             anchor="w", padx=18, pady=11)
        self.label.pack(fill="both", expand=True)
        
        # Bind hover & click to both frame and label
        for widget in (self, self.label):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<Button-1>", self._on_click)
    
    def _on_enter(self, _e):
        if not self.active:
            for w in (self, self.label):
                w.config(bg=Palette.sidebar_hover)
    
    def _on_leave(self, _e):
        if not self.active:
            for w in (self, self.label, self.stripe):
                w.config(bg=Palette.sidebar_bg)
    
    def _on_click(self, _e):
        if self.command:
            self.command()
    
    def set_active(self, active: bool):
        self.active = active
        if active:
            self.stripe.config(bg=Palette.sidebar_active)
            for w in (self, self.label):
                w.config(bg=Palette.sidebar_hover)
            self.label.config(fg=Palette.text)
        else:
            self.stripe.config(bg=Palette.sidebar_bg)
            for w in (self, self.label):
                w.config(bg=Palette.sidebar_bg)
            self.label.config(fg=Palette.muted)
