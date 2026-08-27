"""MNT desktop app: what to hold, what it costs, and whether it ever worked.

WHAT THIS IS FOR

Three questions get asked of a trading model over and over, and each of them is
currently a command line away:

  What would the book hold if I traded it today?
  What does a trade of this size actually cost me?
  Did any of this survive costs, across years I did not choose?

Each is a page here. Nothing in this file computes anything itself - it calls
the same modules the command line does, so a number shown here and a number
printed by walkforward.py cannot drift apart.

WHY EVERYTHING RUNS IN A THREAD

Building the panel takes twenty seconds and a walk-forward takes minutes. Doing
either on the UI thread freezes the window, Windows greys it out and reports the
application as unresponsive, and the user reasonably concludes it has crashed.
Long work happens on a worker thread and reports back through a queue that the
UI polls; the window stays live and the run can be watched.

Run with:  py -3.13 gui.py
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

import re

import pages as pages_module
from theme import Button, Card, Chart, Palette, SidebarButton, fonts, style_widgets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
           else BASE_DIR)


class Worker:
    """Runs callables off the UI thread and posts results back to a queue."""

    def __init__(self, root: tk.Misc):
        self.root = root
        self.queue: queue.Queue = queue.Queue()
        self.root.after(80, self._drain)

    def submit(self, function, on_done, on_error=None) -> None:
        def run():
            try:
                self.queue.put((on_done, function()))
            except BaseException as error:                      # noqa: BLE001
                self.queue.put((on_error or (lambda e: None), error))

        threading.Thread(target=run, daemon=True).start()

    def _drain(self) -> None:
        try:
            while True:
                callback, payload = self.queue.get_nowait()
                try:
                    callback(payload)
                except tk.TclError:
                    # The widgets that asked for this are gone - a theme switch
                    # rebuilds every one of them - so there is nothing left to
                    # deliver the answer to. Dropped rather than raised: the
                    # result is stale, not wrong.
                    pass
        except queue.Empty:
            pass
        finally:
            # Rescheduled from a finally, because a callback that raised used
            # to escape _drain altogether and the pump never ran again. One
            # error in one page's handler left the whole window deaf to every
            # background result after it, with nothing on screen to say so.
            self.root.after(80, self._drain)


class Page(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=Palette.bg)
        self.app = app
        self.f = fonts()

    def on_show(self) -> None:
        """Called each time the page becomes visible."""


class PicksPage(Page):
    """Today's book: the names the model would hold, and why it is allowed to."""

    def __init__(self, parent, app):
        super().__init__(parent, app)

        top = tk.Frame(self, bg=Palette.bg)
        top.pack(fill="x", pady=(0, 12))
        tk.Label(top, text="Today's book", bg=Palette.bg, fg=Palette.text,
                 font=self.f["h1"]).pack(side="left")
        self.run_button = Button(top, "Compute holdings", self.compute)
        self.run_button.pack(side="right")
        self.status = tk.Label(top, text="", bg=Palette.bg, fg=Palette.muted,
                               font=self.f["small"])
        self.status.pack(side="right", padx=12)

        tk.Label(self,
                 text="The names the model would hold today, best score "
                      "first. Press Compute holdings to work them out from "
                      "the latest prices. Nothing here places an order - "
                      "Orders does that.",
                 bg=Palette.bg, fg=Palette.faint, font=self.f["small"],
                 anchor="w", justify="left", wraplength=900).pack(
            fill="x", pady=(0, 12))

        self.stats = tk.Frame(self, bg=Palette.bg)
        self.stats.pack(fill="x", pady=(0, 12))
        self.tiles = {}
        for key, label in (("names", "positions"), ("each", "per position"),
                           ("cost", "round trip"), ("breakeven", "must move")):
            tile = Card(self.stats)
            tile.pack(side="left", expand=True, fill="both", padx=(0, 10))
            value = tk.Label(tile.body, text="-", bg=Palette.panel,
                             fg=Palette.text, font=self.f["number"])
            value.pack(anchor="w")
            tk.Label(tile.body, text=label, bg=Palette.panel, fg=Palette.muted,
                     font=self.f["small"]).pack(anchor="w")
            self.tiles[key] = value

        card = Card(self, "Holdings",
                    "ranked by model score, highest first")
        card.pack(fill="both", expand=True)
        columns = ("rank", "symbol", "score", "mom_126", "vol_21")
        self.tree = ttk.Treeview(card.body, columns=columns, show="headings",
                                 height=12)
        for column, width, heading in (
                ("rank", 50, "#"), ("symbol", 130, "SYMBOL"),
                ("score", 110, "SCORE"), ("mom_126", 110, "6M MOM RANK"),
                ("vol_21", 110, "21D VOL RANK")):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width,
                             anchor="w" if column == "symbol" else "center")
        self.tree.pack(fill="both", expand=True)

        self.note = tk.Label(card.body, text="", bg=Palette.panel,
                             fg=Palette.faint, font=self.f["small"],
                             justify="left", wraplength=760)
        self.note.pack(anchor="w", pady=(10, 0))

    def compute(self) -> None:
        self.run_button.set_enabled(False)
        self.status.config(text="loading prices and scoring...")
        self.app.worker.submit(self._work, self._done, self._failed)

    def _work(self):
        import config
        import costs as costs_module
        import features as features_module
        import production

        _, bundle = production.load()
        panel = features_module.cross_sectionalize(features_module.build_panel())
        latest = production.scored(panel)

        capital = self.app.capital
        each = capital / config.TOP_K
        return {
            "rows": latest.head(config.TOP_K),
            "date": panel["timestamp"].max(),
            "model": bundle["name"],
            "each": each,
            "cost": costs_module.cost_bp(each),
            "breakeven": costs_module.breakeven_move_pct(each),
            "k": config.TOP_K,
        }

    def _done(self, result) -> None:
        self.run_button.set_enabled(True)
        self.status.config(
            text=f"{result['model']}  |  as of {result['date'].date()}")
        self.tiles["names"].config(text=str(result["k"]))
        self.tiles["each"].config(text=f"{result['each']:,.0f}")
        self.tiles["cost"].config(text=f"{result['cost']:.0f}bp")
        self.tiles["breakeven"].config(text=f"{result['breakeven']:.2f}%")

        self.tree.delete(*self.tree.get_children())
        for position, (_, row) in enumerate(result["rows"].iterrows(), start=1):
            self.tree.insert("", "end", values=(
                position, row["symbol"], f"{row['score']:+.4f}",
                f"{row['mom_126']:+.2f}", f"{row['vol_21']:+.2f}"))

        self.note.config(
            text="Feature values are cross-sectional ranks in [-1, 1], not raw "
                 "numbers: +1.00 is the strongest name in the universe that "
                 "day. These are the model's picks, not advice - the "
                 "walk-forward t-statistic on this edge is below 2, so treat "
                 "the ordering as a hypothesis.")

    def _failed(self, error) -> None:
        self.run_button.set_enabled(True)
        self.status.config(text=str(error)[:80], fg=Palette.bad)


class CostsPage(Page):
    """The charge sheet, and how brutally it scales down."""

    def __init__(self, parent, app):
        super().__init__(parent, app)

        tk.Label(self, text="Cost of a round trip", bg=Palette.bg,
                 fg=Palette.text, font=self.f["h1"]).pack(anchor="w",
                                                          pady=(0, 12))

        tk.Label(self,
                 text="What one buy-then-sell costs in charges and "
                      "slippage. The big number is how far the price must "
                      "move before you break even, so 34 bp means 0.34%. "
                      "Fixed charges do not shrink, so small positions pay "
                      "proportionally more - that is what the curve shows.",
                 bg=Palette.bg, fg=Palette.faint, font=self.f["small"],
                 anchor="w", justify="left", wraplength=900).pack(
            fill="x", pady=(0, 12))

        controls = Card(self, "Position", "NSE cash equity, retail discount broker")
        controls.pack(fill="x", pady=(0, 12))

        row = tk.Frame(controls.body, bg=Palette.panel)
        row.pack(fill="x")
        self.value = tk.DoubleVar(value=83333)
        self.segment = tk.StringVar(value="delivery")
        self.slippage = tk.DoubleVar(value=5.0)

        self.readout = tk.Label(row, text="", bg=Palette.panel, fg=Palette.text,
                                font=self.f["number"])
        self.readout.pack(side="left")
        self.sub = tk.Label(row, text="", bg=Palette.panel, fg=Palette.muted,
                            font=self.f["small"])
        self.sub.pack(side="left", padx=(12, 0), pady=(10, 0))

        picker = tk.Frame(controls.body, bg=Palette.panel)
        picker.pack(fill="x", pady=(10, 0))
        for label in ("delivery", "intraday"):
            tk.Radiobutton(picker, text=label, value=label,
                           variable=self.segment, command=self.refresh,
                           bg=Palette.panel, fg=Palette.text,
                           selectcolor=Palette.bg, activebackground=Palette.panel,
                           activeforeground=Palette.text, font=self.f["body"],
                           highlightthickness=0, bd=0).pack(side="left",
                                                            padx=(0, 14))

        tk.Label(picker, text="slippage bp", bg=Palette.panel, fg=Palette.muted,
                 font=self.f["small"]).pack(side="left", padx=(20, 6))
        ttk.Spinbox(picker, from_=0, to=100, increment=1, width=5,
                   textvariable=self.slippage, command=self.refresh, font=self.f["mono_small"]).pack(side="left")

        scale = ttk.Scale(controls.body, from_=2000, to=1000000,
                          variable=self.value, command=lambda _v: self.refresh(),
                          style="Horizontal.TScale")
        scale.pack(fill="x", pady=(14, 0))

        panes = tk.Frame(self, bg=Palette.bg)
        panes.pack(fill="both", expand=True)

        breakdown = Card(panes, "Where the money goes")
        breakdown.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.tree = ttk.Treeview(breakdown.body, columns=("charge", "buy", "sell"),
                                 show="headings", height=9)
        for column, width, heading in (("charge", 130, "CHARGE"),
                                       ("buy", 100, "BUY"), ("sell", 100, "SELL")):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width,
                             anchor="w" if column == "charge" else "e")
        self.tree.pack(fill="both", expand=True)

        curve = Card(panes, "Cost by position size",
                     "fixed charges do not scale down")
        curve.pack(side="left", fill="both", expand=True)
        self.chart = Chart(curve.body, height=250)
        self.chart.pack(fill="both", expand=True)

        self.refresh()

    def refresh(self) -> None:
        import costs as costs_module

        value = max(self.value.get(), 1000)
        segment = self.segment.get()
        slippage = self.slippage.get()

        trip = costs_module.round_trip(value, segment=segment,
                                       slippage_bp=slippage)
        self.readout.config(text=f"{trip.basis_points:.1f} bp")
        self.sub.config(text=f"on Rs {value:,.0f}  =  Rs {trip.total:,.0f}  "
                             f"=  {trip.basis_points / 100:.2f}% to break even")

        self.tree.delete(*self.tree.get_children())
        for (name, buy), (_, sell) in zip(trip.buy.items(), trip.sell.items()):
            if buy or sell:
                self.tree.insert("", "end", values=(name, f"{buy:,.2f}",
                                                    f"{sell:,.2f}"))
        self.tree.insert("", "end", values=("TOTAL", f"{trip.buy.total:,.2f}",
                                            f"{trip.sell.total:,.2f}"))

        sizes = [5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000]
        series = [costs_module.cost_bp(s, segment, slippage) for s in sizes]
        self.chart.line(series, labels=["5k", "10L"], fill=True,
                        formatter=lambda v: f"{v:,.0f}bp")


_RE_BP = re.compile(r"^[+-]\d+$")
_RE_ACC = re.compile(r"^[+-]\d*\.\d+$")


class BacktestPage(Page):
    """Run the walk-forward and read the verdict, without leaving the window."""

    def __init__(self, parent, app):
        super().__init__(parent, app)

        top = tk.Frame(self, bg=Palette.bg)
        top.pack(fill="x", pady=(0, 12))
        tk.Label(top, text="Walk-forward", bg=Palette.bg, fg=Palette.text,
                 font=self.f["h1"]).pack(side="left")

        import config as config_module

        # Only what can actually run. A packaged build excludes torch and
        # tabpfn (see MNT.spec), and offering a choice that raises
        # ModuleNotFoundError several minutes into a walk-forward is worse than
        # not offering it. From source, where both import, all three show.
        import importlib.util

        available = ["lightgbm"]
        for name, module in (("nn", "torch"), ("tabpfn", "tabpfn")):
            if importlib.util.find_spec(module) is not None:
                available.append(name)

        self.signal = tk.StringVar(value=config_module.PRODUCTION_SIGNAL)
        combo = ttk.Combobox(top, textvariable=self.signal, width=10,
                             state="readonly", values=tuple(available))
        combo.pack(side="right", padx=(10, 0))
        self.fast = tk.BooleanVar(value=False)
        # ttk, not tk: tk.Checkbutton draws its indicator natively and ignored
        # the palette, leaving a white Windows box in both schemes.
        ttk.Checkbutton(top, text="fast mode", variable=self.fast,
                        style="TCheckbutton").pack(side="right", padx=(10, 0))
        self.run_button = Button(top, "Run", self.run)
        self.run_button.pack(side="right")

        self.progress = ttk.Progressbar(self, mode="indeterminate",
                                        style="TProgressbar")

        panes = tk.Frame(self, bg=Palette.bg)
        panes.pack(fill="both", expand=True)

        left = Card(panes, "Excess return by test year",
                    "basis points per period, after costs")
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.chart = Chart(left.body, height=260)
        self.chart.pack(fill="both", expand=True)
        self.scores = tk.Label(left.body, text="edge -    acc -", anchor="w",
                               bg=Palette.panel, fg=Palette.muted,
                               font=self.f["mono_small"])
        self.scores.pack(fill="x", pady=(8, 0))

        right = Card(panes, "Output")
        right.pack(side="left", fill="both", expand=True)
        # Palette.inset, not Palette.bg: this sits inside a card, and in the
        # light scheme the window colour is a mid grey that reads as a hole.
        self.log = tk.Text(right.body, bg=Palette.inset, fg=Palette.muted,
                           font=self.f["mono_small"], relief="flat", wrap="none",
                           insertbackground=Palette.text, height=14)
        self.log.pack(fill="both", expand=True)

    def run(self) -> None:
        self.run_button.set_enabled(False)
        self.progress.pack(fill="x", pady=(0, 10), before=self.log.master.master.master)
        self.progress.start(12)
        self.log.delete("1.0", "end")

        if getattr(sys, "frozen", False):
            command = [sys.executable, "--walkforward",
                       "--signal", self.signal.get()]
        else:
            command = [sys.executable, "-u", "walkforward.py",
                       "--signal", self.signal.get()]
        if self.fast.get():
            command += ["--fast", "--max-context", "1000"]

        def work():
            environment = dict(os.environ, PYTHONUNBUFFERED="1")
            process = subprocess.Popen(
                command, cwd=RUN_DIR, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1, env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lines = []
            for line in process.stdout:
                lines.append(line)
                self.app.worker.queue.put((self._append, line))
            process.wait()
            return lines

        self.app.worker.submit(work, self._finished, self._failed)

    def _append(self, line: str) -> None:
        self.log.insert("end", line)
        self.log.see("end")

    def _finished(self, lines) -> None:
        self.run_button.set_enabled(True)
        self.progress.stop()
        self.progress.pack_forget()

        # Pull the per-year excess column out of the table the script printed,
        # rather than recomputing it here - one source of truth.
        years, values, accs = [], [], []
        edge_text = "-"
        for line in lines:
            parts = line.split()
            if parts[:1] == ["EDGE"]:
                edge_text = " ".join(parts[1:3])
                if "'" in line:
                    edge_text += " vs " + line.split("'")[1]
                continue
            if len(parts) >= 6 and parts[0].isdigit() and len(parts[0]) == 4:
                excess = [t for t in parts if _RE_BP.match(t)]
                acc = [t for t in parts if _RE_ACC.match(t)]
                if excess:
                    years.append(parts[0][2:])
                    values.append(float(excess[0]))
                if acc:
                    accs.append(float(acc[-1]))
        if values:
            self.chart.bars(values, years, formatter=lambda v: f"{v:,.0f}")
        mean_acc = f"{sum(accs) / len(accs):+.3f}" if accs else "-"
        self.scores.configure(text=f"edge {edge_text}    acc {mean_acc}")

    def _failed(self, error) -> None:
        self.run_button.set_enabled(True)
        self.progress.stop()
        self.progress.pack_forget()
        self._append(f"\nfailed: {error}\n")


class App(tk.Tk):
    # Grouped: what to hold, what it costs, what it did, and the plumbing.
    PAGES = (("Book", PicksPage),
             ("Orders", pages_module.OrdersPage),
             ("Account", pages_module.AccountPage),
             ("Sim", pages_module.SimPage),
             ("Costs", CostsPage),
             ("Backtest", BacktestPage),
             ("Signals", pages_module.SignalsPage),
             ("Pulse", pages_module.PulsePage),
             ("News", pages_module.NewsPage),
             ("Venues", pages_module.VenuePage),
             ("Universe", pages_module.UniversePage),
             ("Settings", pages_module.SettingsPage))

    def __init__(self):
        super().__init__()
        self.title("MNT")
        self.geometry("1160x740")
        self.minsize(960, 640)
        self.worker = Worker(self)
        self.capital = 500000.0
        self.current = "Costs"
        self._build()

    def apply_theme(self, name: str) -> None:
        """Switch palette and rebuild. Colours are construction arguments in
        tkinter, so there is nothing to restyle in place - every widget has to
        be made again. The current page is remembered across the rebuild so the
        switch does not also navigate somewhere else."""
        import theme as theme_module

        theme_module.set_theme(name)
        theme_module.save_theme(name)
        for child in list(self.winfo_children()):
            child.destroy()
        self._build()

    def _build(self):
        self.configure(bg=Palette.bg)
        style_widgets(self)

        self.f = fonts()

        # Pages reach settings through the app rather than importing at module
        # scope, so building the window never pays for the data modules.
        # Named `settings`, NOT `config`: tk.Misc.config is the widget
        # configuration method, and shadowing it on the root window breaks
        # every internal call that expects it.
        import config as config_module
        self.settings = config_module

        sidebar = tk.Frame(self, bg=Palette.sidebar_bg, width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # A hairline between nav and content. Invisible in the dark scheme,
        # where the sidebar is already darker than the page, and load-bearing
        # in the light one, where both surfaces are white and the two regions
        # otherwise run into each other.
        tk.Frame(self, bg=Palette.border, width=1).pack(side="left", fill="y")

        brand = tk.Frame(sidebar, bg=Palette.sidebar_bg)
        brand.pack(fill="x", pady=(28, 30), padx=22)
        tk.Label(brand, text="MNT", bg=Palette.sidebar_bg, fg=Palette.text,
                 font=self.f["h1"]).pack(anchor="w")
        tk.Label(brand, text="NSE equity book", bg=Palette.sidebar_bg,
                 fg=Palette.faint, font=self.f["small"]).pack(anchor="w")

        tk.Frame(sidebar, bg=Palette.border, height=1).pack(fill="x", padx=16, pady=(0, 12))

        container = tk.Frame(self, bg=Palette.bg)
        container.pack(side="left", fill="both", expand=True, padx=26, pady=24)

        self.pages, self.buttons = {}, {}
        for name, factory in self.PAGES:
            page = factory(container, self)
            self.pages[name] = page

            button = SidebarButton(sidebar, name, command=lambda n=name: self.show(n))
            button.pack(fill="x")
            self.buttons[name] = button

        footer = tk.Label(sidebar, text="costs are policy rates\ncheck your broker",
                          bg=Palette.sidebar_bg, fg=Palette.faint,
                          font=self.f["small"], justify="left")
        footer.pack(side="bottom", anchor="w", padx=20, pady=18)

        # Built now, packed only if a newer release is found. A button that is
        # always visible and usually does nothing trains the operator to ignore
        # it, so absence is the message.
        self.update_info = None
        self.update_holder = tk.Frame(sidebar, bg=Palette.sidebar_bg)
        self.update_holder.pack(side="bottom", fill="x", padx=16, pady=(0, 4))
        self.update_button = Button(self.update_holder, "Update available",
                                    self.open_update, kind="ghost")
        self._check_update()

        # Whatever was open before, not a fixed page: a theme switch is made
        # from Settings, and rebuilding onto Costs would answer the click by
        # navigating away from it.
        self.show(self.current if self.current in self.pages else "Costs")

    def _check_update(self) -> None:
        if not getattr(self.settings, "UPDATE_REPO", ""):
            return
        self.worker.submit(self._update_work, self._update_done,
                           lambda _error: None)

    def _update_work(self):
        import update

        return update.check(self.settings.APP_VERSION,
                            self.settings.UPDATE_REPO)

    GLOW = ("#1f6feb", "#388bfd", "#58a6ff", "#79c0ff", "#58a6ff",
            "#388bfd")

    def _update_done(self, found) -> None:
        if not found:
            return
        self.update_info = found
        self.update_button.config(text=f"Update to {found['version']}")
        self.update_button.pack(fill="x")
        self._glow()

    def _glow(self, step: int = 0) -> None:
        if not self.update_info:
            return
        colour = self.GLOW[step % len(self.GLOW)]
        button = self.update_button
        button.base = colour
        button.hover = "#79c0ff"
        button.pressed = "#1f6feb"
        try:
            button.config(bg=colour, fg="#ffffff")
        except tk.TclError:
            return
        self.after(420, self._glow, step + 1)

    def open_update(self) -> None:
        import webbrowser

        if self.update_info:
            page = getattr(self.settings, "UPDATE_PAGE", "")
            webbrowser.open_new_tab(page or self.update_info["url"])

    def show(self, name: str) -> None:
        self.current = name
        for other, page in self.pages.items():
            page.pack_forget()
            self.buttons[other].set_active(False)
        self.pages[name].pack(fill="both", expand=True)
        self.buttons[name].set_active(True)
        self.pages[name].on_show()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--walkforward":
        import walkforward as walkforward_module

        sys.argv = ["walkforward"] + sys.argv[2:]
        try:
            walkforward_module.main()
        except SystemExit:
            raise
        except BaseException as error:
            print(f"{type(error).__name__}: {error}")
            sys.exit(1)
        sys.exit(0)
    App().mainloop()
