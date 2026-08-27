"""The operational tabs: paper account, orders, sim, news, pulse, venues.

Kept out of gui.py so that file stays the shell - navigation, theming and the
worker thread - and this one holds the pages that talk to the trading modules.
Every page here follows the same contract: build widgets in __init__, do nothing
expensive until asked, and push slow work onto App.worker.

OrdersPage CAN place live orders, and is the only page that can. It passes
confirm_live to orders.execute only when risk.py reports the gate armed, and
arming is a deliberate act with a deadline. risk.check then vets every order
independently, so an armed gate is permission to try, never permission to skip
the limits. A live placement also raises a modal naming the rupee amount.

Everything else here reads state or runs simulations against the paper account.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from theme import Button, Card, Chart, Palette, fonts


class Page(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=Palette.bg)
        self.app = app
        self.f = fonts()

    def on_show(self) -> None:
        """Called each time the page becomes visible."""

    def header(self, title: str) -> tk.Frame:
        row = tk.Frame(self, bg=Palette.bg)
        row.pack(fill="x", pady=(0, 14))
        tk.Label(row, text=title, bg=Palette.bg, fg=Palette.text,
                 font=self.f["h1"]).pack(side="left")
        return row


class AccountPage(Page):
    """The paper account: equity, positions, and every fill it has taken."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        row = self.header("Paper account")
        self.reset_button = Button(row, "Reset", self.reset, kind="ghost")
        self.reset_button.pack(side="right", padx=(8, 0))
        Button(row, "Refresh", self.refresh, kind="ghost").pack(side="right")

        tiles = tk.Frame(self, bg=Palette.bg)
        tiles.pack(fill="x", pady=(0, 14))
        self.tiles = {}
        for key, label in (("equity", "equity"), ("cash", "cash"),
                           ("pnl", "P&L"), ("trades", "fills")):
            card = Card(tiles)
            card.pack(side="left", expand=True, fill="both", padx=(0, 10))
            value = tk.Label(card.body, text="-", bg=Palette.panel,
                             fg=Palette.text, font=self.f["number"])
            value.pack(anchor="w")
            tk.Label(card.body, text=label, bg=Palette.panel, fg=Palette.muted,
                     font=self.f["small"]).pack(anchor="w")
            self.tiles[key] = value

        panes = tk.Frame(self, bg=Palette.bg)
        panes.pack(fill="both", expand=True)

        left = Card(panes, "Positions")
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.positions = ttk.Treeview(
            left.body, columns=("symbol", "qty", "avg", "last", "pnl"),
            show="headings", height=10)
        for column, width, heading in (("symbol", 110, "SYMBOL"),
                                       ("qty", 60, "QTY"), ("avg", 90, "AVG"),
                                       ("last", 90, "LAST"), ("pnl", 90, "P&L")):
            self.positions.heading(column, text=heading)
            self.positions.column(column, width=width,
                                  anchor="w" if column == "symbol" else "e")
        self.positions.pack(fill="both", expand=True)

        right = Card(panes, "Recent fills")
        right.pack(side="left", fill="both", expand=True)
        self.fills = ttk.Treeview(
            right.body, columns=("time", "side", "symbol", "qty", "price"),
            show="headings", height=10)
        for column, width, heading in (("time", 90, "TIME"), ("side", 50, "SIDE"),
                                       ("symbol", 100, "SYMBOL"),
                                       ("qty", 55, "QTY"), ("price", 85, "PRICE")):
            self.fills.heading(column, text=heading)
            self.fills.column(column, width=width,
                              anchor="w" if column in ("time", "symbol") else "e")
        self.fills.pack(fill="both", expand=True)

    def on_show(self) -> None:
        self.refresh()

    def reset(self) -> None:
        import broker as broker_module

        broker_module.broker("paper").reset(self.app.capital)
        self.refresh()

    def refresh(self) -> None:
        self.app.worker.submit(self._work, self._done, lambda e: None)

    def _work(self):
        import broker as broker_module

        account = broker_module.broker("paper")
        equity, unrealised = account.value()
        rows = []
        for symbol, position in account.holdings().items():
            try:
                last = account.ltp(symbol)
            except Exception:                                   # noqa: BLE001
                last = position["avg"]
            rows.append((symbol, position["qty"], f"{position['avg']:,.2f}",
                         f"{last:,.2f}",
                         f"{(last - position['avg']) * position['qty']:+,.0f}"))
        return {"equity": equity, "cash": account.cash(),
                "opened": account.state.get("opened", equity),
                "positions": rows, "trades": account.state["trades"][-14:],
                "count": len(account.state["trades"])}

    def _done(self, result) -> None:
        pnl = result["equity"] - result["opened"]
        self.tiles["equity"].config(text=f"{result['equity']:,.0f}")
        self.tiles["cash"].config(text=f"{result['cash']:,.0f}")
        self.tiles["pnl"].config(
            text=f"{pnl:+,.0f}",
            fg=Palette.good if pnl >= 0 else Palette.bad)
        self.tiles["trades"].config(text=str(result["count"]))

        self.positions.delete(*self.positions.get_children())
        for row in result["positions"]:
            self.positions.insert("", "end", values=row)

        self.fills.delete(*self.fills.get_children())
        for trade in reversed(result["trades"]):
            self.fills.insert("", "end", values=(
                trade["time"][11:19], trade["side"], trade["symbol"],
                trade["qty"], f"{trade['price']:,.2f}"))


class OrdersPage(Page):
    """Plan a rebalance to the model's book, inspect it, then place it on paper."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        row = self.header("Orders")
        self.place_button = Button(row, "Place", self.place)
        self.place_button.pack(side="right", padx=(8, 0))
        Button(row, "Plan", self.build_plan, kind="ghost").pack(side="right")
        self.place_button.set_enabled(False)

        # --- the live gate -------------------------------------------------
        # Present on the page rather than buried in a menu, because the single
        # most important thing to know before pressing Place is whether this
        # will spend money.
        gate = Card(self, "Live trading", "disarmed by default")
        gate.pack(fill="x", pady=(0, 10))
        line = tk.Frame(gate.body, bg=Palette.panel)
        line.pack(fill="x")

        self.gate_state = tk.Label(line, text="", bg=Palette.panel,
                                   fg=Palette.muted, font=self.f["h2"])
        self.gate_state.pack(side="left")

        self.halt_button = Button(line, "HALT", self.toggle_halt, kind="ghost")
        self.halt_button.pack(side="right", padx=(8, 0))
        self.arm_button = Button(line, "Arm", self.toggle_arm, kind="ghost")
        self.arm_button.pack(side="right")
        self.arm_minutes = tk.IntVar(value=15)
        ttk.Spinbox(line, from_=1, to=240, increment=5, width=4,
                   textvariable=self.arm_minutes, font=self.f["mono_small"]).pack(side="right", padx=(0, 8))
        tk.Label(line, text="minutes", bg=Palette.panel, fg=Palette.faint,
                 font=self.f["small"]).pack(side="right", padx=(0, 6))

        self.gate_detail = tk.Label(gate.body, text="", bg=Palette.panel,
                                    fg=Palette.faint, font=self.f["small"],
                                    justify="left", anchor="w")
        self.gate_detail.pack(fill="x", pady=(6, 0))
        self._gate_timer = None

        self.status = tk.Label(self, text="", bg=Palette.bg, fg=Palette.muted,
                               font=self.f["small"], anchor="w")
        self.status.pack(fill="x", pady=(0, 8))

        card = Card(self, "Plan", "sells first, then buys")
        card.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            card.body, columns=("side", "symbol", "qty", "price", "value",
                                "cost", "reason"),
            show="headings", height=12)
        for column, width, heading in (
                ("side", 60, "SIDE"), ("symbol", 110, "SYMBOL"),
                ("qty", 60, "QTY"), ("price", 90, "PRICE"),
                ("value", 100, "VALUE"), ("cost", 70, "COST"),
                ("reason", 130, "REASON")):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width,
                             anchor="w" if column in ("symbol", "reason", "side")
                             else "e")
        self.tree.pack(fill="both", expand=True)
        self.tree.tag_configure("BUY", foreground=Palette.good)
        self.tree.tag_configure("SELL", foreground=Palette.bad)

        self.note = tk.Label(card.body, text="", bg=Palette.panel,
                             fg=Palette.faint, font=self.f["small"],
                             justify="left", wraplength=800)
        self.note.pack(anchor="w", pady=(10, 0))
        self.plan = []

    # -- live gate ---------------------------------------------------------
    def on_show(self) -> None:
        self.refresh_gate()

    def refresh_gate(self) -> None:
        """Repaint the gate, and keep repainting so the countdown is truthful."""
        import broker as broker_module
        import risk

        venue = broker_module.broker()
        live = getattr(venue, "live", False)
        state = risk.status()

        if state["halted"]:
            text, colour = "HALTED", Palette.bad
        elif not live:
            text, colour = f"PAPER ({venue.name})", Palette.muted
        elif state["armed"]:
            left = state["seconds_left"]
            text = f"ARMED  {left // 60}:{left % 60:02d}"
            colour = Palette.bad
        else:
            text, colour = "DISARMED", Palette.good

        self.gate_state.config(text=text, fg=colour)
        self.arm_button.config(
            text="Disarm" if state["armed"] else "Arm")
        self.halt_button.config(text="Resume" if state["halted"] else "HALT")

        self.place_button.base = (Palette.bad if (live and state["armed"])
                                  else Palette.accent)
        detail = (f"venue {venue.name} | today {state['orders_today']} orders, "
                  f"Rs {state['notional_today']:,.0f} | per order max "
                  f"Rs {state['max_order_value']:,.0f} | day max "
                  f"{state['max_day_orders']} orders")
        if not live:
            detail += "\nPaper venue - arming has no effect. Set MNT_BROKER=groww for live."
        self.gate_detail.config(text=detail)

        if self._gate_timer is not None:
            self.after_cancel(self._gate_timer)
        self._gate_timer = self.after(1000, self.refresh_gate)

    def toggle_arm(self) -> None:
        import risk

        if risk.armed():
            risk.disarm()
            self.status.config(text="disarmed", fg=Palette.muted)
        else:
            try:
                risk.arm(self.arm_minutes.get(), note="gui")
                self.status.config(
                    text=f"ARMED for {self.arm_minutes.get()} minutes - live "
                         f"orders will now be attempted. It lapses on its own.",
                    fg=Palette.bad)
            except risk.Refused as error:
                self.status.config(text=str(error), fg=Palette.bad)
        self.refresh_gate()

    def toggle_halt(self) -> None:
        import risk

        if risk.halted():
            risk.resume()
            self.status.config(text="resumed", fg=Palette.muted)
        else:
            risk.halt("gui")
            self.status.config(text="HALTED - nothing will be placed until "
                                    "resumed", fg=Palette.bad)
        self.refresh_gate()

    def build_plan(self) -> None:
        self.status.config(text="scoring the universe...", fg=Palette.muted)
        self.place_button.set_enabled(False)
        self.app.worker.submit(self._work, self._done, self._failed)

    def _work(self):
        import broker as broker_module
        import orders as orders_module

        targets = orders_module._model_picks()
        venue = broker_module.broker()
        return targets, orders_module.plan(targets, venue), venue.name

    def _done(self, payload) -> None:
        targets, plan, venue = payload
        self.plan = plan
        self.tree.delete(*self.tree.get_children())
        for order in plan:
            self.tree.insert("", "end", tags=(order["side"],), values=(
                order["side"], order["symbol"], order["qty"],
                f"{order['price']:,.2f}", f"{order['value']:,.0f}",
                f"{order['cost_bp']:.0f}bp", order.get("reason", "")))

        cash = sum(o["value"] if o["side"] == "BUY" else -o["value"] for o in plan)
        self.status.config(
            text=f"target book: {', '.join(targets)}   |   venue: {venue}   |   "
                 f"{len(plan)} orders   |   net cash {-cash:+,.0f}")
        self.place_button.set_enabled(bool(plan))
        self.note.config(
            text="On a paper venue these fill against cached prices. On a live "
                 "venue they are CNC limit orders, and every one is re-checked "
                 "by risk.py immediately before it is sent - arming, per-order "
                 "and daily limits, universe membership, and a 3% band against "
                 "the live quote. The whole plan is pre-flighted first, so a "
                 "rebalance that would breach a limit halfway is refused before "
                 "any of it is placed.")

    def _failed(self, error) -> None:
        self.status.config(text=str(error)[:110], fg=Palette.bad)

    def place(self) -> None:
        """Place the plan. On a live venue, arming IS the confirmation.

        confirm_live is passed only when the gate is open, so the two controls
        stay distinct: arming is the deliberate human act, and risk.check still
        independently vets every order after it.
        """
        if not self.plan:
            return

        import broker as broker_module
        import risk

        venue = broker_module.broker()
        live = getattr(venue, "live", False)

        if live and not risk.armed():
            self.status.config(
                text=f"{venue.name} is a LIVE venue and the gate is disarmed. "
                     f"Arm it above to place real orders.", fg=Palette.bad)
            return

        if live:
            total = sum(o["value"] for o in self.plan)
            if not messagebox.askyesno(
                    "Place REAL orders?",
                    f"This will place {len(self.plan)} orders on {venue.name} "
                    f"for about Rs {total:,.0f} of REAL money.\n\n"
                    f"The strategy's walk-forward t-statistic is below 2 and "
                    f"the order-level sim lost to buy-and-hold.\n\n"
                    f"Continue?", icon="warning", default="no"):
                self.status.config(text="cancelled", fg=Palette.muted)
                return

        self.place_button.set_enabled(False)
        self.status.config(text="placing...", fg=Palette.muted)

        def work():
            import orders as orders_module
            return orders_module.execute(self.plan, venue, confirm_live=live)

        self.app.worker.submit(work, self._placed, self._failed)

    def _placed(self, results) -> None:
        # "error" alone is not enough. orders.execute() now classifies each
        # response as filled / partial / rejected / skipped, because a broker
        # can return a perfectly successful HTTP call carrying a rejection
        # inside it. Counting those as filled is how the screen ends up
        # disagreeing with the account, which is the one thing this panel
        # exists to prevent.
        failed = [r for r in results
                  if "error" in r or r.get("status") == "rejected"]
        partial = [r for r in results if r.get("status") == "partial"]
        skipped = [r for r in results if r.get("status") == "skipped"]
        filled = len(results) - len(failed) - len(partial) - len(skipped)

        message = f"{filled} filled"
        if partial:
            message += f"  |  {len(partial)} PARTIAL"
        if skipped:
            message += f"  |  {len(skipped)} already sent today"
        if failed:
            reason = failed[0].get("error") or failed[0].get("status", "rejected")
            message += f"  |  stopped: {str(reason)[:70]}"
        self.status.config(
            text=message,
            fg=Palette.bad if failed else
               (Palette.warn if partial else Palette.good))
        self.plan = []

        # Only refresh automatically when everything went through. build_plan()
        # sets its own "scoring the universe..." status on its first line, so
        # calling it here unconditionally overwrote the result the operator had
        # just been given - including, before this, the "stopped: ..." error.
        # Placement results were effectively invisible. Anything that did not
        # fill cleanly now stays on screen until the operator refreshes, which
        # is the one moment in this app worth interrupting someone for.
        # skipped counts too: it means the same-day duplicate guard fired and
        # stopped an order from going out twice. That is the safety net working
        # and the operator should be told, not have it refreshed away.
        if failed or partial or skipped:
            self.place_button.set_enabled(True)
        else:
            self.build_plan()


class SimPage(Page):
    """Run the order-level simulation and show equity against buy-and-hold."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        row = self.header("Simulation")
        self.run_button = Button(row, "Run sim", self.run)
        self.run_button.pack(side="right", padx=(8, 0))
        self.days = tk.IntVar(value=252)
        ttk.Spinbox(row, from_=60, to=1500, increment=21, width=6,
                   textvariable=self.days, font=self.f["mono_small"]).pack(side="right", padx=(0, 8))
        tk.Label(row, text="sessions", bg=Palette.bg, fg=Palette.muted,
                 font=self.f["small"]).pack(side="right", padx=(0, 6))

        self.progress = ttk.Progressbar(self, mode="indeterminate",
                                        style="TProgressbar")

        tiles = tk.Frame(self, bg=Palette.bg)
        tiles.pack(fill="x", pady=(0, 14))
        self.tiles = {}
        for key, label in (("final", "closing equity"), ("bench", "buy & hold"),
                           ("charges", "charges paid"), ("dd", "max drawdown")):
            card = Card(tiles)
            card.pack(side="left", expand=True, fill="both", padx=(0, 10))
            value = tk.Label(card.body, text="-", bg=Palette.panel,
                             fg=Palette.text, font=self.f["number"])
            value.pack(anchor="w")
            tk.Label(card.body, text=label, bg=Palette.panel, fg=Palette.muted,
                     font=self.f["small"]).pack(anchor="w")
            self.tiles[key] = value

        card = Card(self, "Equity", "traded book, marked at each close")
        card.pack(fill="both", expand=True)
        self.chart = Chart(card.body, height=270)
        self.chart.pack(fill="both", expand=True)
        self.verdict = tk.Label(card.body, text="", bg=Palette.panel,
                                fg=Palette.muted, font=self.f["small"])
        self.verdict.pack(anchor="w", pady=(8, 0))

    def run(self) -> None:
        self.run_button.set_enabled(False)
        self.progress.pack(fill="x", pady=(0, 8))
        self.progress.start(12)
        days, capital = self.days.get(), self.app.capital

        def work():
            import paper as paper_module

            every = self.app.settings.REBALANCE_EVERY
            top_k = self.app.settings.TOP_K
            curve = paper_module.replay(days, capital, every, top_k, quiet=True)
            bench = paper_module.benchmark_curve(curve["timestamp"], capital)
            stats = paper_module.summarise(curve, bench, capital)
            run_id = paper_module.record(stats, every, top_k)
            return curve, bench, capital, stats, run_id

        self.app.worker.submit(work, self._done, self._failed)

    def _done(self, payload) -> None:
        curve, bench, capital, stats, run_id = payload
        self.run_button.set_enabled(True)
        self.progress.stop()
        self.progress.pack_forget()

        equity = curve["equity"].tolist()
        drawdown = stats["max_drawdown_pct"]
        final = stats["final_equity"]
        bench_final = stats["benchmark_equity"]
        charges = stats["charges_paid"]
        fills = stats["trades"]

        self.tiles["final"].config(
            text=f"{final:,.0f}",
            fg=Palette.good if final >= capital else Palette.bad)
        self.tiles["bench"].config(text=f"{bench_final:,.0f}")
        self.tiles["charges"].config(text=f"{charges:,.0f}")
        self.tiles["dd"].config(text=f"{drawdown:.1f}%", fg=Palette.bad)

        labels = [str(curve["timestamp"].iloc[0].date()),
                  str(curve["timestamp"].iloc[-1].date())]
        self.chart.line(equity, labels=labels, baseline=capital,
                        colour=Palette.good if final >= capital else Palette.bad,
                        formatter=lambda v: f"{v / 1000:,.0f}k")
        logged = f" Logged as run {run_id}." if run_id else ""
        self.verdict.config(
            text=(f"{stats['sessions']} sessions, {fills} fills. "
                  + ("Beat buy-and-hold." if final >= bench_final else
                     f"Lost to buy-and-hold by Rs {bench_final - final:,.0f} - "
                     f"charges alone were "
                     f"{stats['charges_pct_capital']:.2f}% of capital.")
                  + logged))

    def _failed(self, error) -> None:
        self.run_button.set_enabled(True)
        self.progress.stop()
        self.progress.pack_forget()
        self.verdict.config(text=str(error)[:140], fg=Palette.bad)


class NewsPage(Page):
    """Headlines for whatever the account holds, with the crude score attached."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        row = self.header("News")
        Button(row, "Fetch", self.fetch).pack(side="right")

        finder = tk.Frame(row, bg=Palette.bg)
        finder.pack(side="left", padx=(24, 0))
        self.query = tk.Entry(finder, bg=Palette.panel_high, fg=Palette.text,
                              font=self.f["mono_small"], relief="flat",
                              insertbackground=Palette.text, width=26)
        self.query.pack(side="left", ipady=4)
        self.query.bind("<Return>", lambda _event: self.search())
        self.search_button = Button(finder, "Search news", self.search,
                                    kind="ghost")
        self.search_button.pack(side="left", padx=(8, 0))

        self._timer = None
        self.every = tk.IntVar(value=15)
        self.auto = tk.BooleanVar(value=False)
        ttk.Spinbox(row, from_=1, to=180, increment=5, width=4,
                   textvariable=self.every, font=self.f["mono_small"]).pack(side="right", padx=(0, 8))
        tk.Checkbutton(row, text="auto every", variable=self.auto,
                       command=self.auto_toggle, bg=Palette.bg,
                       fg=Palette.muted, selectcolor=Palette.panel,
                       activebackground=Palette.bg,
                       activeforeground=Palette.text, font=self.f["small"],
                       highlightthickness=0, bd=0).pack(side="right", padx=(0, 4))
        tk.Label(row, text="min", bg=Palette.bg, fg=Palette.faint,
                 font=self.f["small"]).pack(side="right", padx=(0, 10))

        self.status = tk.Label(row, text="", bg=Palette.bg, fg=Palette.muted,
                               font=self.f["small"])
        self.status.pack(side="right", padx=12)

        card = Card(self, "Headlines", "Google News RSS")
        card.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(card.body, columns=("score", "symbol", "title"),
                                 show="headings", height=16)
        for column, width, heading in (("score", 60, "SCORE"),
                                       ("symbol", 110, "SYMBOL"),
                                       ("title", 1400, "HEADLINE")):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, minwidth=width,
                             stretch=False,
                             anchor="center" if column == "score" else "w")
        xscroll = ttk.Scrollbar(card.body, orient="horizontal",
                                command=self.tree.xview)
        self.tree.configure(xscrollcommand=xscroll.set)
        self.tree.pack(fill="both", expand=True)
        xscroll.pack(fill="x")
        self.tree.tag_configure("pos", foreground=Palette.good)
        self.tree.tag_configure("neg", foreground=Palette.bad)

        tk.Label(card.body,
                 text="Sentiment is a positive-minus-negative word count. It is "
                      "not a signal and nothing in this project trades on it.",
                 bg=Palette.panel, fg=Palette.faint,
                 font=self.f["small"]).pack(anchor="w", pady=(10, 0))
        self.items = []
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Double-1>", self._open)

        self.related = Card(self, "More on this stock",
                            "double-click any headline to open it")
        self.related.pack(fill="x", pady=(14, 0))
        self.related_tree = ttk.Treeview(
            self.related.body,
            columns=("score", "symbol", "published", "title"),
            show="headings", height=8)
        for column, width, heading, anchor_ in (
                ("score", 60, "SCORE", "center"),
                ("symbol", 110, "SYMBOL", "w"),
                ("published", 190, "PUBLISHED", "w"),
                ("title", 1100, "HEADLINE", "w")):
            self.related_tree.heading(column, text=heading)
            self.related_tree.column(column, width=width, minwidth=width,
                                     stretch=False, anchor=anchor_)
        rscroll = ttk.Scrollbar(self.related.body, orient="horizontal",
                                command=self.related_tree.xview)
        self.related_tree.configure(xscrollcommand=rscroll.set)
        self.related_tree.pack(fill="x")
        rscroll.pack(fill="x")
        self.related_tree.tag_configure("stripe", background=Palette.stripe)
        self.related_tree.tag_configure("other", foreground=Palette.muted)
        self.related_tree.bind("<Double-1>", self._open_related)
        self.related_rows = []

        self.related_note = tk.Label(
            self.related.body, text="Click a headline to see the rest for "
                                    "that stock.",
            bg=Palette.panel, fg=Palette.muted, font=self.f["small"],
            anchor="w", justify="left", wraplength=900)
        self.related_note.pack(fill="x", pady=(8, 0))

    def auto_toggle(self) -> None:
        """Start or stop the repeating fetch.

        Scheduled with `after`, not a thread with a sleep in it: the fetch
        itself already runs on the worker, and a sleeping thread would keep the
        interpreter alive after the window closed.
        """
        if self.auto.get():
            self.fetch()
            self._schedule()
        elif self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None

    def _schedule(self) -> None:
        if not self.auto.get():
            return
        minutes = max(int(self.every.get()), 1)
        self._timer = self.after(minutes * 60_000, self._tick)

    def _tick(self) -> None:
        self.fetch()
        self._schedule()

    def fetch(self) -> None:
        self.status.config(text="fetching...", fg=Palette.muted)

        def work():
            import news as news_module

            symbols = news_module._held_or_default()
            known = news_module.seen_titles()
            items = news_module.collect(symbols, limit=5, quiet=True)
            fresh = [i for i in items if i["title"] not in known]
            news_module.record(fresh)
            return items, len(fresh)

        self.app.worker.submit(work, self._done, self._failed)

    def _done(self, payload) -> None:
        from datetime import datetime

        items, fresh = payload
        self.items = items
        self.related_rows = []
        self.related_tree.delete(*self.related_tree.get_children())
        self.related_note.config(text="Click a headline to see the rest for "
                                      "that stock.")
        self.tree.delete(*self.tree.get_children())
        for item in sorted(items, key=lambda i: -i["score"]):
            tag = "pos" if item["score"] > 0 else "neg" if item["score"] < 0 else ""
            self.tree.insert("", "end", tags=(tag,), values=(
                f"{item['score']:+d}" if item["score"] else "0",
                item["symbol"], item["title"]))
        when = datetime.now().strftime("%H:%M:%S")
        suffix = f", {fresh} new" if self.auto.get() else ""
        if not items:
            import config

            self.status.config(
                text=("No stocks yet - add them in the Universe tab."
                      if not config.UNIVERSE
                      else f"No headlines returned  ({when})"),
                fg=Palette.muted)
            return
        self.status.config(text=f"{len(items)} headlines{suffix}  ({when})",
                           fg=Palette.muted)

    def search(self) -> None:
        text = self.query.get().strip()
        if not text:
            self.status.config(text="Type a symbol or company name first.",
                               fg=Palette.muted)
            return
        self.search_button.set_enabled(False)
        self.status.config(text=f"Looking for news on {text}...",
                           fg=Palette.muted)

        def work():
            import config
            import news as news_module

            symbol = config.valid_symbol(text)
            if symbol:
                items = news_module.fetch(symbol, limit=12)
                if items:
                    return symbol, items
            matches = config.search_symbols(text, limit=1)
            if matches and matches[0]["exchange"] != "-":
                found = matches[0]["symbol"]
                return found, news_module.fetch(found, limit=12)
            return (symbol or text.upper()), []

        self.app.worker.submit(work, self._searched, self._search_failed)

    def _searched(self, payload) -> None:
        symbol, items = payload
        self.search_button.set_enabled(True)
        self._done((items, 0))
        if items:
            self.status.config(text=f"{len(items)} headlines for {symbol}",
                               fg=Palette.muted)
        else:
            self.status.config(text=f"No headlines found for {symbol}",
                               fg=Palette.muted)

    def _search_failed(self, error) -> None:
        self.search_button.set_enabled(True)
        self.status.config(text=f"{type(error).__name__}: {error}"[:90],
                           fg=Palette.bad)

    def _failed(self, error) -> None:
        self.status.config(text=str(error)[:80], fg=Palette.bad)

    def _selected_item(self):
        selection = self.tree.selection()
        if not selection:
            return None
        values = self.tree.item(selection[0], "values")
        for item in self.items:
            if item["symbol"] == values[1] and item["title"] == values[2]:
                return item
        return None

    def _select(self, _event=None) -> None:
        item = self._selected_item()
        if item:
            self._fill_related(item)

    def _fill_related(self, chosen) -> None:
        symbol = chosen["symbol"]
        same = [i for i in self.items
                if i["symbol"] == symbol and i["title"] != chosen["title"]]
        others = [i for i in self.items if i["symbol"] != symbol]
        rows = same + others
        self.related_rows = rows
        self.related_tree.delete(*self.related_tree.get_children())
        for index, item in enumerate(rows):
            tags = ["stripe"] if index % 2 else []
            if item["symbol"] != symbol:
                tags.append("other")
            self.related_tree.insert(
                "", "end", tags=tuple(tags),
                values=(f"{item['score']:+d}" if item["score"] else "0",
                        item["symbol"],
                        (item.get("published") or "")[:22], item["title"]))
        self.related_note.config(
            text=(f"{len(same)} more on {symbol}, then {len(others)} from "
                  f"the rest of the book."))

    def _launch(self, item) -> None:
        import webbrowser

        link = (item.get("link") or "").strip()
        if not link:
            self.related_note.config(text="That headline carries no link.")
            return
        webbrowser.open_new_tab(link)

    def _open(self, _event=None) -> None:
        item = self._selected_item()
        if item:
            self._launch(item)

    def _open_related(self, _event=None) -> None:
        selection = self.related_tree.selection()
        if not selection:
            return
        index = self.related_tree.index(selection[0])
        if 0 <= index < len(self.related_rows):
            self._launch(self.related_rows[index])


class PulsePage(Page):
    """Dispersion and breadth - the conditions that decide whether ranking pays."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        row = self.header("Pulse")
        Button(row, "Snapshot", self.snapshot).pack(side="right")

        tk.Label(self,
                 text="Whether conditions suit a ranking model at all. "
                      "Dispersion is how far apart the names are moving - "
                      "wide is good, because ranking needs winners and "
                      "losers to separate. High average correlation is bad: "
                      "everything moving together leaves nothing to rank. "
                      "Above 50-DMA and India VIX are breadth and fear. "
                      "Press Snapshot to record one reading.",
                 bg=Palette.bg, fg=Palette.faint, font=self.f["small"],
                 anchor="w", justify="left", wraplength=900).pack(
            fill="x", pady=(0, 12))

        tiles = tk.Frame(self, bg=Palette.bg)
        tiles.pack(fill="x", pady=(0, 14))
        self.tiles = {}
        for key, label in (("dispersion_1d", "dispersion 1d"),
                           ("avg_correlation", "avg correlation"),
                           ("pct_above_50dma", "above 50-DMA"),
                           ("vix", "India VIX")):
            card = Card(tiles)
            card.pack(side="left", expand=True, fill="both", padx=(0, 10))
            value = tk.Label(card.body, text="-", bg=Palette.panel,
                             fg=Palette.text, font=self.f["number"])
            value.pack(anchor="w")
            tk.Label(card.body, text=label, bg=Palette.panel, fg=Palette.muted,
                     font=self.f["small"]).pack(anchor="w")
            self.tiles[key] = value

        panes = tk.Frame(self, bg=Palette.bg)
        panes.pack(fill="both", expand=True)

        left = Card(panes, "Reading")
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.text = tk.Text(left.body, bg=Palette.panel, fg=Palette.muted,
                            font=self.f["mono_small"], relief="flat",
                            wrap="word", height=12)
        self.text.pack(fill="both", expand=True)

        right = Card(panes, "Recorded dispersion", "one point per snapshot")
        right.pack(side="left", fill="both", expand=True)
        self.chart = Chart(right.body, height=240)
        self.chart.pack(fill="both", expand=True)

    def on_show(self) -> None:
        if not self.text.get("1.0", "end").strip():
            self.snapshot()

    def snapshot(self) -> None:
        def work():
            import pulse as pulse_module

            snapshot = pulse_module.take()
            pulse_module.record(snapshot)
            return snapshot, pulse_module.summary(snapshot), pulse_module.history()

        self.app.worker.submit(work, self._done, lambda e: None)

    def _done(self, payload) -> None:
        snapshot, summary, history = payload
        for key, formatter in (("dispersion_1d", "{:.2%}"),
                               ("avg_correlation", "{:.2f}"),
                               ("pct_above_50dma", "{:.0%}"),
                               ("vix", "{:.2f}")):
            raw = snapshot.get(key)
            self.tiles[key].config(
                text=formatter.format(raw) if isinstance(raw, (int, float))
                else "-")

        self.text.delete("1.0", "end")
        self.text.insert("1.0", summary)

        series = [row["dispersion_1d"] for row in history
                  if isinstance(row.get("dispersion_1d"), float)]
        if len(series) >= 2:
            self.chart.line(series, formatter=lambda v: f"{v:.1%}")
        else:
            self.chart.line([], formatter=lambda v: f"{v:.1%}")


class VenuePage(Page):
    """Enter API keys, see what is configured, and the refusal to trade live.

    Entry fields show a mask, never the stored value. A field whose value comes
    from an environment variable is shown disabled: the environment wins in
    credentials.effective(), so letting you type over it would present a change
    that silently does nothing.
    """

    def __init__(self, parent, app):
        super().__init__(parent, app)
        row = self.header("API keys")
        self.status = tk.Label(row, text="", bg=Palette.bg, fg=Palette.muted,
                               font=self.f["small"])
        self.status.pack(side="right", padx=12)

        outer = tk.Frame(self, bg=Palette.bg)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=Palette.bg, highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self.inner = tk.Frame(canvas, bg=Palette.bg)
        self.inner.bind("<Configure>", lambda _e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.entries: dict[tuple[str, str], tk.Entry] = {}
        self.headers: dict[str, tk.Label] = {}
        self._build()

    def _build(self) -> None:
        import credentials

        for name, spec in credentials.PROVIDERS.items():
            card = Card(self.inner, spec["title"],
                        "broker" if spec["kind"] == "broker" else "model")
            card.pack(fill="x", pady=(0, 12))

            state = tk.Label(card.body, text="", bg=Palette.panel,
                             fg=Palette.muted, font=self.f["small"],
                             justify="left", wraplength=760, anchor="w")
            state.pack(fill="x", pady=(0, 10))
            self.headers[name] = state

            for field, label, variable, help_text in spec["fields"]:
                line = tk.Frame(card.body, bg=Palette.panel)
                line.pack(fill="x", pady=3)
                tk.Label(line, text=label, bg=Palette.panel, fg=Palette.text,
                         font=self.f["body"], width=16, anchor="w").pack(
                    side="left")
                entry = tk.Entry(line, bg=Palette.panel_high, fg=Palette.text,
                                 font=self.f["mono_small"], relief="flat",
                                 insertbackground=Palette.text, show="")
                entry.pack(side="left", fill="x", expand=True, ipady=4)
                tk.Label(line, text=variable, bg=Palette.panel,
                         fg=Palette.faint, font=self.f["mono_small"],
                         width=26, anchor="e").pack(side="left", padx=(10, 0))
                self.entries[(name, field)] = entry
                tk.Label(card.body, text=f"    {help_text}", bg=Palette.panel,
                         fg=Palette.faint, font=self.f["small"],
                         anchor="w", justify="left", wraplength=740).pack(
                    fill="x")

            buttons = tk.Frame(card.body, bg=Palette.panel)
            buttons.pack(fill="x", pady=(10, 0))
            Button(buttons, "Save", lambda n=name: self.save(n)).pack(side="left")
            Button(buttons, "Forget", lambda n=name: self.forget(n),
                   kind="ghost").pack(side="left", padx=(8, 0))

        policy = Card(self.inner, "Why ordering stays on paper")
        policy.pack(fill="x")
        tk.Label(
            policy.body, justify="left", anchor="w", wraplength=780,
            bg=Palette.panel, fg=Palette.muted, font=self.f["body"],
            text=("Saving broker credentials does NOT enable live trading. The "
                  "live adapters raise NotImplementedError on every method that "
                  "would touch an account, and orders.execute refuses any live "
                  "venue unless confirm_live=True is passed in code - which this "
                  "window never does.\n\n"
                  "That is deliberate. The walk-forward t-statistic is below 2, "
                  "the order-level sim lost to buy-and-hold, and charges "
                  "alone consumed about 2% of capital in a year.\n\n"
                  f"Keys are stored as plain JSON at {credentials.store_path()}, "
                  "outside the project folder, readable by anyone with your "
                  "Windows login. Environment variables override saved values.")
        ).pack(anchor="w")

    def on_show(self) -> None:
        import credentials

        for name, spec in credentials.PROVIDERS.items():
            values = credentials.effective(name)
            ready = spec["usable"](values)
            self.headers[name].config(
                text=("READY - " if ready else "") + spec["explain"](values),
                fg=Palette.good if ready else Palette.muted)

            for field, _, _, _ in spec["fields"]:
                entry = self.entries[(name, field)]
                where = credentials.source(name, field)
                entry.config(state="normal")
                entry.delete(0, "end")
                if where == "environment":
                    entry.insert(0, f"{credentials.masked(values[field])}  "
                                    f"(from environment)")
                    entry.config(state="disabled",
                                 disabledbackground=Palette.panel,
                                 disabledforeground=Palette.faint)
                elif where == "saved":
                    entry.insert(0, credentials.masked(values[field]))

    def save(self, name: str) -> None:
        import credentials

        updates = {}
        for field, _, _, _ in credentials.provider(name)["fields"]:
            entry = self.entries[(name, field)]
            if str(entry.cget("state")) == "disabled":
                continue
            typed = entry.get().strip()
            # A masked placeholder means "unchanged" - saving it would store
            # the asterisks as if they were the key.
            if not typed or set(typed) <= {"*"} or "*" * 8 in typed:
                continue
            updates[field] = typed

        if not updates:
            self.status.config(text="nothing changed", fg=Palette.muted)
            return

        path = credentials.save(name, updates)
        credentials.apply_to_environment(name)
        self.status.config(text=f"saved {len(updates)} field(s) to {path}",
                           fg=Palette.good)
        self.on_show()

    def forget(self, name: str) -> None:
        import credentials

        removed = credentials.forget(name)
        self.status.config(
            text=f"{name}: removed" if removed else f"{name}: nothing stored",
            fg=Palette.muted)
        self.on_show()


class SignalsPage(Page):
    """What to do today, and whether the names involved are still holdable.

    NAMED FOR THE PAGE, NOT THE MODULE. `signals.py` in this project is the
    pluggable model-signal interface - NeuralSignal, TabPFNSignal, BoostedSignal
    - and it is older than this page and imported by production.py,
    walkforward.py and sizing_test.py. Nothing here is called signals.py and
    nothing here imports it; the two words mean different things one directory
    apart, which is worth keeping straight.

    TWO VIEWS, IN THE ORDER THEY MATTER

    WHAT TO DO is the difference between the model's book and the account, via
    orders.plan - the same arithmetic the Orders page runs, shown read-only.
    This page has no arm switch, no dry run and no send button, and never calls
    orders.execute. risk.armed() is READ so the state is visible; it is never
    changed from here.

    OTHER NAMES is the screen. The book half is the production question:
    config.UNIVERSE is a fixed list of thirty names chosen years ago, and
    nothing else in this project would notice if one quietly stopped being
    liquid or appeared on an exchange surveillance list. The candidates half is
    browsing, and it is second because config.py records a measurement against
    acting on it - fitting on 150 names instead of 29 cost the book 1.32
    points. A name that survives the screen has earned a measurement, not a
    place in the book.

    The screen does not consult the model, deliberately. The ranker was trained
    to order names by forward return and has never been asked whether one is
    tradeable; borrowing its opinion for a safety question would be this page
    claiming something the model does not know.
    """

    def __init__(self, parent, app):
        super().__init__(parent, app)
        row = self.header("Signals")

        # One primary action, whose meaning follows the view. "Other names"
        # used to sit here as well and duplicated the segmented control below,
        # so the page offered two different-looking routes to the same table.
        self.run_button = Button(row, "Refresh", self.refresh)
        self.run_button.pack(side="right")
        self.update_button = Button(row, "Update ASM/GSM",
                                    self.update_surveillance, kind="ghost")
        self.update_button.pack(side="right", padx=(0, 8))

        # Segmented switch rather than a nested notebook: both views are tables
        # that want the whole height, and MNT navigates with a sidebar already.
        switch = tk.Frame(self, bg=Palette.bg)
        switch.pack(fill="x", pady=(0, 10))
        self.todo_button = Button(switch, "What to do",
                                  lambda: self.show_view("todo"))
        self.todo_button.pack(side="left", padx=(0, 6))
        self.book_button = Button(switch, "Screen: my book", self.run_book,
                                  kind="ghost")
        self.book_button.pack(side="left", padx=(0, 6))
        self.cand_button = Button(switch, "Screen: other names",
                                  self.run_candidates, kind="ghost")
        self.cand_button.pack(side="left")

        # A status strip rather than a floating line of monospace: it is a
        # persistent statement about the account, so it gets a surface.
        self.gate_strip = tk.Frame(self, bg=Palette.panel,
                                   highlightthickness=1,
                                   highlightbackground=Palette.border)
        self.gate_strip.pack(fill="x", pady=(0, 10))
        self.gate = tk.Label(self.gate_strip, text="", bg=Palette.panel,
                             fg=Palette.muted, font=self.f["label"],
                             anchor="w", justify="left", padx=14, pady=8)
        self.gate.pack(fill="x")

        self.source = tk.Label(self, text="", bg=Palette.bg, fg=Palette.warn,
                               font=self.f["small"], anchor="w",
                               justify="left", wraplength=900)

        self.view = "todo"
        self.scope_name = "book"
        self._build_todo()

        self.card = Card(self, "Names", "liquidity, pattern flags, surveillance")

        columns = ("symbol", "price", "turnover", "screen", "why")
        self.tree = ttk.Treeview(self.card.body, columns=columns,
                                 show="headings", height=16)
        for column, width, heading, anchor in (
                ("symbol", 110, "SYMBOL", "w"), ("price", 90, "PRICE", "e"),
                ("turnover", 100, "TURNOVER", "e"),
                ("screen", 80, "SCREEN", "center"), ("why", 420, "WHY", "w")):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, anchor=anchor)
        self.tree.tag_configure("avoid", foreground=Palette.bad)
        self.tree.tag_configure("caution", foreground=Palette.warn)
        self.tree.tag_configure("listed", foreground=Palette.bad)
        self.tree.tag_configure("stripe", background=Palette.stripe)
        self.tree.pack(fill="both", expand=True)

        self.summary = tk.Label(self.card.body, text="", bg=Palette.panel,
                                fg=Palette.faint, font=self.f["small"],
                                anchor="w", justify="left", wraplength=900)
        self.summary.pack(anchor="w", fill="x", pady=(10, 0))

        self._mark_scope()
        self.show_view("todo")

    # ------------------------------------------------------------------
    # what to do
    # ------------------------------------------------------------------

    def _build_todo(self) -> None:
        self.todo_card = Card(self, "What to do",
                              "model book differenced against the account")

        columns = ("side", "symbol", "qty", "price", "value", "cost", "reason")
        self.todo_tree = ttk.Treeview(self.todo_card.body, columns=columns,
                                      show="headings", height=16)
        for column, width, heading, anchor in (
                ("side", 70, "SIDE", "center"), ("symbol", 120, "SYMBOL", "w"),
                ("qty", 80, "QTY", "e"), ("price", 100, "PRICE", "e"),
                ("value", 120, "VALUE", "e"), ("cost", 80, "COST bp", "e"),
                ("reason", 160, "REASON", "w")):
            self.todo_tree.heading(column, text=heading)
            self.todo_tree.column(column, width=width, anchor=anchor)
        # Background bands, not coloured text - see Palette.buy_row.
        self.todo_tree.tag_configure("BUY", background=Palette.buy_row)
        self.todo_tree.tag_configure("SELL", background=Palette.sell_row)
        self.todo_tree.pack(fill="both", expand=True)

        self.todo_summary = tk.Label(self.todo_card.body, text="",
                                     bg=Palette.panel, fg=Palette.faint,
                                     font=self.f["small"], anchor="w",
                                     justify="left", wraplength=900)
        self.todo_summary.pack(anchor="w", fill="x", pady=(10, 0))

    def show_view(self, which: str) -> None:
        """Swap which table is packed. The other one keeps its rows."""
        self.view = which
        self._mark_scope()

        self.todo_card.pack_forget()
        self.card.pack_forget()
        self.source.pack_forget()
        if which == "todo":
            self.todo_card.pack(fill="both", expand=True)
        else:
            self.source.pack(fill="x", pady=(0, 10))
            self.card.pack(fill="both", expand=True)

    def refresh(self) -> None:
        """Recompute whichever view is showing, at whatever scope it is on."""
        if self.view == "screen":
            # Re-run the scope already displayed. Always calling run_book here
            # would silently throw away a candidate scan the moment anyone
            # pressed the page's primary button.
            self._run(lambda m: getattr(m, self.scope_name)(), self.scope_name)
            return

        self.run_button.set_enabled(False)
        self.todo_summary.config(text="Scoring the universe and reading the "
                                      "account. Takes about a minute.")

        def work():
            import broker as broker_module
            import orders as orders_module
            import production

            picks = production.picks()
            venue = broker_module.broker()
            plan = orders_module.plan(picks, venue=venue)
            equity = venue.cash() + sum(
                position["qty"] * position.get("avg", 0.0)
                for position in venue.holdings().values())
            return picks, plan, float(equity)

        self.app.worker.submit(work, self._todo_done, self._todo_failed)

    def _todo_failed(self, error) -> None:
        self.run_button.set_enabled(True)
        self.todo_summary.config(text=f"{type(error).__name__}: {error}")

    def _todo_done(self, payload) -> None:
        picks, plan, equity = payload
        self.run_button.set_enabled(True)
        self.todo_tree.delete(*self.todo_tree.get_children())
        for order in plan:
            self.todo_tree.insert(
                "", "end", tags=(order["side"],),
                values=(order["side"], order["symbol"], f"{order['qty']:,}",
                        f"{order.get('price', 0.0):,.2f}",
                        f"{order.get('value', 0.0):,.0f}",
                        f"{order.get('cost_bp', 0.0):,.1f}",
                        order.get("reason", "")))

        traded = sum(o.get("value", 0.0) for o in plan)
        buys = sum(1 for o in plan if o["side"] == "BUY")
        self.todo_summary.config(
            text=(f"{buys} to buy, {len(plan) - buys} to sell, "
                  f"{traded:,.0f} traded against {equity:,.0f} equity.  "
                  f"Book: {', '.join(picks)}.  Read-only."))
        self._refresh_gate()

    def _refresh_gate(self) -> None:
        """Read the live gate. Read only - nothing here ever changes it."""
        import risk

        armed, halted = risk.armed(), risk.halted()
        state = "ARMED" if armed else "closed"
        if armed:
            state += f" ({risk.arm_remaining()}m left)"
        self.gate.config(
            text=f"live gate {state}" + ("   HALTED" if halted else ""),
            fg=Palette.bad if (armed or halted) else Palette.muted)

    # ------------------------------------------------------------------
    # screen
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        import screen as screen_module

        self.source.config(text=screen_module.describe_source())
        self._refresh_gate()
        if self.view == "screen" and not self.tree.get_children():
            self.run_book()
        elif self.view == "todo" and not self.todo_tree.get_children():
            self.refresh()

    def _mark_scope(self) -> None:
        active = "todo" if self.view == "todo" else self.scope_name
        for button, name in ((self.todo_button, "todo"),
                             (self.book_button, "book"),
                             (self.cand_button, "candidates")):
            button.configure(bg=Palette.accent if name == active
                             else Palette.panel_high)

    def run_book(self) -> None:
        self.scope_name = "book"
        self._mark_scope()
        self.show_view("screen")
        self._run(lambda m: m.book(), "book")

    def run_candidates(self) -> None:
        self.scope_name = "candidates"
        self._mark_scope()
        self.show_view("screen")
        self._run(lambda m: m.candidates(), "candidates")

    def _run(self, pick, what: str) -> None:
        self.run_button.set_enabled(False)
        self.summary.config(
            text=f"Screening the {what}. Names without a cached history need a "
                 f"fetch, so the first run is slow.")

        def work():
            import screen as screen_module

            return what, pick(screen_module), screen_module.describe_source()

        self.app.worker.submit(work, self._done, self._failed)

    def update_surveillance(self) -> None:
        """Pull today's ASM/GSM from NSE. A failure leaves the old file alone."""
        self.update_button.set_enabled(False)
        self.summary.config(text="Asking NSE for the ASM and GSM lists...")

        def work():
            import screen as screen_module

            return (screen_module.fetch_surveillance(),
                    screen_module.describe_source())

        self.app.worker.submit(work, self._updated, self._update_failed)

    def _update_failed(self, error) -> None:
        self.update_button.set_enabled(True)
        self.summary.config(text=f"Update failed: {type(error).__name__}: "
                                 f"{error}  The previous list is untouched.")

    def _updated(self, payload) -> None:
        result, source = payload
        self.update_button.set_enabled(True)
        self.source.config(text=source)
        if result["ok"]:
            # Not re-screened automatically: the rows on screen were judged
            # against the previous list, and leaving them under a banner saying
            # the list is current would misdescribe them.
            self.summary.config(
                text=f"ASM/GSM updated: {result['rows']} symbols. Screen again "
                     f"- the rows above were judged against the previous list.")
        else:
            self.summary.config(
                text=f"Update failed: {result['error']}  The previous list, if "
                     f"any, is untouched.")

    def _failed(self, error) -> None:
        self.run_button.set_enabled(True)
        self.summary.config(text=f"{type(error).__name__}: {error}")

    def _done(self, payload) -> None:
        what, rows, source = payload
        self.run_button.set_enabled(True)
        self.source.config(text=source)
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(rows):
            # Striping only where nothing more important is being said. A
            # severity tag and a stripe tag on the same row fight over the
            # background, and the stripe is the one that does not matter.
            tag = "listed" if row["listed"] else (
                row["severity"] if row["severity"] != "clean"
                else ("stripe" if index % 2 else ""))
            # An unknown turnover is printed as unknown, never as 0.0 - a zero
            # here would read as "measured, and it is nothing".
            turnover = ("unknown" if row["turnover"] is None
                        else f"{row['turnover'] / 1e7:,.1f} Cr")
            self.tree.insert(
                "", "end", tags=(tag,) if tag else (),
                values=(row["symbol"], f"{row['price']:,.2f}", turnover,
                        row["severity"],
                        row["verdict"] if row["listed"] else row["flags"]))

        flagged = sum(1 for r in rows if r["severity"] != "clean")
        listed = sum(1 for r in rows if r["listed"])
        if not rows:
            import config

            self.summary.config(
                text=("No stocks yet - add them in the Universe tab."
                      if not config.UNIVERSE
                      else f"{what}: nothing to screen."))
            return
        self.summary.config(
            text=(f"{what}: {len(rows)} screened, {flagged} with flags, "
                  f"{listed} on a surveillance list.  Clean means no flag "
                  f"tripped, not cleared."))


class SettingsPage(Page):
    """Appearance, and the diagnostics that do not belong in the main flow.

    THE DEBUG SECTION IS HERE ON PURPOSE

    The cache inspector used to be a top-level "Data" page, sitting in the
    navigation between News and Venues as though checking whether a CSV is
    stale were a daily trading task. It is not; it is something you go looking
    for when a number looks wrong. Moving it here takes it out of the way
    without throwing it away - which matters, because it is still the only
    thing in this app that would tell you a symbol's bars stopped updating.

    THEMES CANNOT BE REPAINTED IN PLACE

    Every widget in tkinter takes its colours as construction arguments, so a
    running window cannot be recoloured - there is no stylesheet to swap. The
    switch therefore rebuilds the window, and the choice is written to
    artifacts/ui.json so the next launch opens the way this one ended.
    """

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.header("Settings")

        outer = tk.Frame(self, bg=Palette.bg)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=Palette.bg, highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=Palette.bg)
        body.bind("<Configure>", lambda _e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        def wheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")

        self._wheel = wheel
        self._scroll_canvas = canvas
        self._scroll_body = body
        for widget in (canvas, body):
            widget.bind("<MouseWheel>", wheel)

        # --- appearance ---------------------------------------------------
        appearance = Card(body, "Appearance", "applies immediately")
        appearance.pack(fill="x", pady=(0, 14))

        row = tk.Frame(appearance.body, bg=Palette.panel)
        row.pack(fill="x")
        tk.Label(row, text="Theme", bg=Palette.panel, fg=Palette.text,
                 font=self.f["body"], width=16, anchor="w").pack(side="left")

        import theme as theme_module

        self.theme_buttons = {}
        for name, label in (("dark", "Dark"), ("light", "Light")):
            button = Button(row, label, lambda n=name: self.set_theme(n),
                            kind="ghost")
            button.pack(side="left", padx=(0, 8))
            self.theme_buttons[name] = button

        tk.Label(appearance.body,
                 text="Light is a Groww-style scheme: white cards, teal-green "
                      "accent. Switching rebuilds the window - any table you "
                      "had loaded is cleared, nothing else is lost.",
                 bg=Palette.panel, fg=Palette.faint, font=self.f["small"],
                 anchor="w", justify="left", wraplength=820).pack(
            fill="x", pady=(10, 0))

        # --- model --------------------------------------------------------
        model_card = Card(body, "Model", "what the book actually trades")
        model_card.pack(fill="x", pady=(0, 14))

        row = tk.Frame(model_card.body, bg=Palette.panel)
        row.pack(fill="x")
        tk.Label(row, text="Signal", bg=Palette.panel, fg=Palette.text,
                 font=self.f["body"], width=16, anchor="w").pack(side="left")

        self.signal_buttons = {}
        import importlib.util

        choices = [("lightgbm", "LightGBM")]
        if importlib.util.find_spec("torch") is not None:
            choices.append(("nn", "Neural net"))
        for name, label in choices:
            button = Button(row, label, lambda n=name: self.set_signal(n),
                            kind="ghost")
            button.pack(side="left", padx=(0, 8))
            self.signal_buttons[name] = button

        self.refit_button = Button(row, "Refit now", self.refit, kind="ghost")
        self.refit_button.pack(side="right")

        self.signal_note = tk.Label(
            model_card.body, text="", bg=Palette.panel, fg=Palette.muted,
            font=self.f["small"], anchor="w", justify="left", wraplength=820)
        self.signal_note.pack(fill="x", pady=(10, 0))

        tk.Label(model_card.body,
                 text="Measured over 14 walk-forward folds, same features, "
                      "same target, same turnover machinery: LightGBM +46bp "
                      "excess per period at t 1.76, the network less. The "
                      "default is LightGBM because that is what the "
                      "measurement chose - switching here runs the other one, "
                      "it does not make it the better model. Changing the "
                      "signal leaves the saved fit behind, so refit before the "
                      "Book, Orders or Sim tabs are trusted.",
                 bg=Palette.panel, fg=Palette.faint, font=self.f["small"],
                 anchor="w", justify="left", wraplength=820).pack(
            fill="x", pady=(8, 0))

        # --- stocks -------------------------------------------------------
        stocks_card = Card(body, "Stocks", "managed in the Universe tab")
        stocks_card.pack(fill="x", pady=(0, 14))

        tk.Label(stocks_card.body,
                 text="Add, remove and search for stocks in the Universe tab. "
                      "A name in the UNIVERSE is fitted, ranked and can be "
                      "bought; a WATCHLIST name is fetched and shown and never "
                      "reaches the model. Names live in artifacts/stocks.json "
                      "and survive a reinstall; refit before the Book or "
                      "Orders tabs are trusted.",
                 bg=Palette.panel, fg=Palette.faint, font=self.f["small"],
                 anchor="w", justify="left", wraplength=820).pack(
            fill="x", pady=(2, 0))

        self._mark_theme()
        self._mark_signal()
        self._bind_wheel(self._scroll_body)

    def _bind_wheel(self, widget) -> None:
        if isinstance(widget, ttk.Treeview):
            return
        widget.bind("<MouseWheel>", self._wheel)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    # ------------------------------------------------------------------

    def _stored_signal(self) -> str:
        """The name stamped into the saved fit, without loading it properly.

        production.load() deliberately refuses when the stored model and the
        configured one disagree - which is exactly the state this page has to
        be able to describe. So the stamp is read directly; a missing or
        unreadable bundle is reported as such rather than raised.
        """
        try:
            import joblib

            import production as production_module

            if not os.path.exists(production_module.PATH):
                return "none"
            return joblib.load(production_module.PATH).get("name") or "unknown"
        except Exception:
            return "unreadable"

    def _mark_signal(self) -> None:
        current = getattr(self.app.settings, "PRODUCTION_SIGNAL", "lightgbm")
        for name, button in self.signal_buttons.items():
            button.configure(bg=Palette.accent if name == current
                             else Palette.panel_high,
                             fg=Palette.on_accent if name == current
                             else Palette.text)

        stored = self._stored_signal()
        if stored == current:
            self.signal_note.config(
                text=f"Trading {current}; the saved fit is {stored}.",
                fg=Palette.good)
            self.refit_button.set_enabled(True)
        else:
            self.signal_note.config(
                text=(f"Set to {current}, but the saved fit is {stored}. "
                      f"Book, Orders and Sim will refuse to run until this is "
                      f"refitted."),
                fg=Palette.warn)
            self.refit_button.set_enabled(True)

    def set_signal(self, name: str) -> None:
        import theme as theme_module

        if name == getattr(self.app.settings, "PRODUCTION_SIGNAL", None):
            return
        theme_module.save_signal(name)
        self.app.settings.PRODUCTION_SIGNAL = name
        self._mark_signal()

    def refit(self) -> None:
        """Fit and save the configured signal, so the stamp matches again."""
        name = getattr(self.app.settings, "PRODUCTION_SIGNAL", "lightgbm")
        self.refit_button.set_enabled(False)
        self.signal_note.config(text=f"Fitting {name}...", fg=Palette.muted)

        def work():
            import production as production_module

            signal, _panel = production_module.fit(name, quiet=True)
            production_module.save(signal, name)
            return name

        self.app.worker.submit(work, self._refitted, self._refit_failed)

    def _refitted(self, name) -> None:
        self.refit_button.set_enabled(True)
        self._mark_signal()

    def _refit_failed(self, error) -> None:
        self.refit_button.set_enabled(True)
        self.signal_note.config(text=str(error)[:200], fg=Palette.bad)

    def _mark_theme(self) -> None:
        import theme as theme_module

        current = getattr(theme_module.Palette, "name", "dark")
        for name, button in self.theme_buttons.items():
            button.configure(bg=Palette.accent if name == current
                             else Palette.panel_high,
                             fg=Palette.on_accent if name == current
                             else Palette.text)

    def set_theme(self, name: str) -> None:
        if name == getattr(Palette, "name", "dark"):
            return
        self.app.apply_theme(name)

    def on_show(self) -> None:
        self._mark_signal()
        self._bind_wheel(self._scroll_body)



class UniversePage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)

        self.card = Card(self, "Universe", "every name the book can see")
        self.card.pack(fill="both", expand=True)

        columns = ("symbol", "sector", "source", "list")
        self.tree = ttk.Treeview(self.card.body, columns=columns,
                                 show="headings", height=11)
        for column, width, heading, anchor in (
                ("symbol", 140, "SYMBOL", "w"),
                ("sector", 220, "SECTOR", "w"),
                ("source", 110, "SOURCE", "center"),
                ("list", 110, "LIST", "center")):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, anchor=anchor)
        self.tree.tag_configure("stripe", background=Palette.stripe)
        self.tree.tag_configure("watch", foreground=Palette.muted)
        scroll = ttk.Scrollbar(self.card.body, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Double-1>", self._detail)

        row = tk.Frame(self.card.body, bg=Palette.panel)
        row.pack(fill="x", pady=(12, 0))
        tk.Label(row, text="Symbol", bg=Palette.panel, fg=Palette.text,
                 font=self.f["body"], width=16, anchor="w").pack(side="left")
        self.entry = tk.Entry(row, bg=Palette.panel_high, fg=Palette.text,
                              font=self.f["mono_small"], relief="flat",
                              insertbackground=Palette.text)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda _event: self.search())

        self.results = ttk.Treeview(self.card.body,
                                    columns=("symbol", "name", "exchange"),
                                    show="headings", height=5)
        for column, width, heading, anchor in (
                ("symbol", 140, "SYMBOL", "w"),
                ("name", 420, "COMPANY", "w"),
                ("exchange", 90, "LISTED", "center")):
            self.results.heading(column, text=heading)
            self.results.column(column, width=width, anchor=anchor)
        self.results.tag_configure("stripe", background=Palette.stripe)
        self.results.bind("<<TreeviewSelect>>", self._pick_result)
        self.results_shown = False

        buttons = tk.Frame(self.card.body, bg=Palette.panel)
        buttons.pack(fill="x", pady=(10, 0))
        self.search_button = Button(buttons, "Search", self.search,
                                    kind="ghost")
        self.search_button.pack(side="left", padx=(0, 8))
        self.add_universe_button = Button(
            buttons, "Add to universe", lambda: self.add(False), kind="ghost")
        self.add_universe_button.pack(side="left", padx=(0, 8))
        self.add_watch_button = Button(
            buttons, "Add to watchlist", lambda: self.add(True), kind="ghost")
        self.add_watch_button.pack(side="left", padx=(0, 8))
        self.remove_button = Button(buttons, "Remove", self.remove,
                                    kind="ghost")
        self.remove_button.pack(side="left")
        self.detail_button = Button(buttons, "Details",
                                    self._detail, kind="ghost")
        self.detail_button.pack(side="left", padx=(8, 0))
        self._force_symbol = None
        self.force_button = Button(buttons, "Add anyway", self.add_force,
                                   kind="ghost")
        self.force_button.pack(side="left", padx=(8, 0))
        self.force_button.set_enabled(False)

        self.note = tk.Label(self.card.body, text="", bg=Palette.panel,
                             fg=Palette.muted, font=self.f["small"],
                             anchor="w", justify="left", wraplength=820)
        self.note.pack(fill="x", pady=(10, 0))

        self.help_label = tk.Label(
            self.card.body,
            text="A UNIVERSE name is fitted, ranked and can be bought. A "
                      "WATCHLIST name is fetched and shown and never reaches "
                      "the model. Every name here is one you added, so any of them "
                      "can be removed. Names live in artifacts/stocks.json and "
                      "survive a reinstall; refit before the Book or Orders "
                      "tabs are trusted.",
            bg=Palette.panel, fg=Palette.faint, font=self.f["small"],
            anchor="w", justify="left", wraplength=820)
        self.help_label.pack(fill="x", pady=(8, 0))

        self._fill()

    def _rows(self):
        import config

        base = {s.upper() for s in config.BASE_UNIVERSE}
        watch = {s.upper() for s in config.WATCHLIST}
        seen, rows = set(), []
        for name in list(config.UNIVERSE) + list(config.WATCHLIST):
            key = name.upper()
            if key in seen:
                continue
            seen.add(key)
            rows.append((name,
                         config.SECTORS.get(name) or "-",
                         "built in" if key in base else "added",
                         "watchlist" if key in watch else "universe"))
        rows.sort(key=lambda item: (item[3] == "watchlist", item[0]))
        return rows

    def _fill(self, message: str = "") -> None:
        import config

        self.tree.delete(*self.tree.get_children())
        for index, values in enumerate(self._rows()):
            tags = ["stripe"] if index % 2 else []
            if values[3] == "watchlist":
                tags.append("watch")
            self.tree.insert("", "end", values=values, tags=tuple(tags))
        summary = (f"Universe {len(config.UNIVERSE)} "
                   f"({len(config.USER_UNIVERSE)} added)"
                   f"   |   Watchlist {len(config.WATCHLIST)}")
        self.note.config(text=f"{message}   {summary}" if message else summary)
        self._selection_buttons()

    def _selected(self):
        items = self.tree.selection()
        return self.tree.item(items[0], "values") if items else None

    def _select(self, _event=None) -> None:
        values = self._selected()
        if values:
            self.entry.delete(0, "end")
            self.entry.insert(0, values[0])
        self._selection_buttons()

    def _detail(self, _event=None) -> None:
        values = self._selected()
        symbol = values[0] if values else self.entry.get().strip().upper()
        if not symbol:
            self._fill("Pick a stock first, then Details.")
            return
        StockDetail(self.app, symbol)

    def _selection_buttons(self) -> None:
        values = self._selected()
        self.remove_button.set_enabled(not values or values[2] != "built in")

    def _buttons(self, enabled: bool) -> None:
        self.add_universe_button.set_enabled(enabled)
        self.add_watch_button.set_enabled(enabled)
        self.remove_button.set_enabled(enabled)
        self.force_button.set_enabled(enabled and bool(self._force_symbol))

    def search(self) -> None:
        query = self.entry.get().strip()
        if not query:
            self._fill("Type a company or ticker first, e.g. Divi or DIVISLAB.")
            return
        self.search_button.set_enabled(False)
        self.note.config(text=f"Searching NSE for {query!r}...")

        def work():
            import config

            return config.search_symbols(query)

        self.app.worker.submit(work, self._searched, self._search_failed)

    def _searched(self, rows) -> None:
        self.search_button.set_enabled(True)
        self.results.delete(*self.results.get_children())
        if not rows:
            self._show_results(False)
            self._fill(f"Nothing on NSE matched {self.entry.get().strip()!r}.")
            return
        for index, row in enumerate(rows):
            self.results.insert("", "end", tags=("stripe",) if index % 2 else (),
                                values=(row["symbol"], row["name"],
                                        row["exchange"]))
        self._show_results(True)
        self._fill(f"{len(rows)} matches - pick one, then Add to universe or "
                   f"watchlist.")

    def _search_failed(self, error) -> None:
        self.search_button.set_enabled(True)
        self._show_results(False)
        self._fill(f"{type(error).__name__}: {error}")

    def _show_results(self, visible: bool) -> None:
        if visible and not self.results_shown:
            self.results.pack(fill="x", pady=(10, 0),
                              before=self.help_label)
        elif not visible and self.results_shown:
            self.results.pack_forget()
        self.results_shown = visible

    def _pick_result(self, _event=None) -> None:
        items = self.results.selection()
        if not items:
            return
        self.entry.delete(0, "end")
        self.entry.insert(0, self.results.item(items[0], "values")[0])

    def add(self, watchlist: bool) -> None:
        import config

        symbol = config.valid_symbol(self.entry.get())
        if not symbol:
            self._fill("Type an NSE symbol first, e.g. DIVISLAB.")
            return
        self._force_symbol = None
        self._buttons(False)
        self.note.config(text=f"Checking {symbol} for tradeable history...")
        self.app.worker.submit(lambda: self._work(symbol, watchlist),
                               self._done, self._failed)

    def _work(self, symbol: str, watchlist: bool):
        import config

        if watchlist:
            label = config.detect_sector(symbol)
            added, message = config.add_stock(symbol, watchlist=True,
                                              sector=label)
            return added, message, None
        ok, detail = config.verify_symbol(symbol)
        if not ok:
            short = symbol if "of history, needs" in detail else None
            if short:
                detail += "  Press Add anyway to force it in."
            return False, detail, short
        label = config.detect_sector(symbol)
        added, message = config.add_stock(symbol, watchlist=False,
                                          sector=label)
        return added, f"{detail} {message}", None

    def _done(self, payload) -> None:
        ok, detail, short = payload
        self._force_symbol = short
        self._buttons(True)
        if ok:
            self.entry.delete(0, "end")
        self._fill(detail)

    def add_force(self) -> None:
        symbol = self._force_symbol
        if not symbol:
            return
        self._buttons(False)
        self.note.config(text=f"Adding {symbol} without the history check...")
        self.app.worker.submit(lambda: self._force_work(symbol),
                               self._done, self._failed)

    def _force_work(self, symbol: str):
        import config

        label = config.detect_sector(symbol)
        added, message = config.add_stock(symbol, watchlist=False,
                                          sector=label)
        note = (f"{message} Forced in without the history check - the model "
                f"cannot score it until it has a year of bars.")
        return added, note, None

    def _failed(self, error) -> None:
        self._buttons(True)
        self._fill(f"{type(error).__name__}: {error}")

    def remove(self) -> None:
        import config

        ok, detail = config.remove_stock(self.entry.get())
        if ok:
            self.entry.delete(0, "end")
        self._fill(detail)

    def on_show(self) -> None:
        self._fill()


_SCORES = None


class StockDetail(tk.Toplevel):
    def __init__(self, app, symbol: str):
        super().__init__(app, bg=Palette.bg, padx=18, pady=18)
        self.app = app
        self.symbol = symbol
        self.f = fonts()
        self.closes = []
        self.dates = []
        self.colour = None
        self.title(f"{symbol} - MNT")
        self.geometry("900x640")

        head = tk.Frame(self, bg=Palette.bg)
        head.pack(fill="x")
        tk.Label(head, text=symbol, bg=Palette.bg, fg=Palette.text,
                 font=self.f["h1"]).pack(side="left")
        self.sector = tk.Label(head, text="", bg=Palette.bg, fg=Palette.muted,
                               font=self.f["body"])
        self.sector.pack(side="left", padx=(12, 0))

        self.stats = tk.Label(self, text="Reading cached history...",
                              bg=Palette.bg, fg=Palette.muted,
                              font=self.f["mono_small"], anchor="w",
                              justify="left")
        self.stats.pack(fill="x", pady=(12, 0))

        card = Card(self, "Price", "daily closes from the local cache")
        card.pack(fill="both", expand=True, pady=(14, 0))
        self.chart = Chart(card.body, height=320)
        self.chart.pack(fill="both", expand=True)

        self.model = tk.Label(self, text="Model view: reading the model...",
                              bg=Palette.bg, fg=Palette.muted,
                              font=self.f["body"], anchor="w", justify="left",
                              wraplength=860)
        self.model.pack(fill="x", pady=(12, 0))

        self.app.worker.submit(self._work, self._done, self._failed)
        self.app.worker.submit(self._score_work, self._score_done,
                               self._score_failed)

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

    def _done(self, payload) -> None:
        closes = payload["closes"]
        self.closes = closes[-1000:]
        self.dates = payload["dates"][-1000:]
        self.sector.config(text=payload["sector"])
        if not closes:
            self.stats.config(text="No cached price history for this name yet.")
            return
        window = closes[-252:]
        position = payload["position"]
        quantity = position.get("qty", 0) or 0
        average = float(position.get("avg", 0.0) or 0.0)
        money = (f"{quantity:,} @ {average:,.2f} = {quantity * average:,.0f}"
                 if quantity else "nothing held")
        turnover = payload["turnover"]
        self.stats.config(text=(
            f"Last {closes[-1]:,.2f}     "
            f"52w {min(window):,.2f} - {max(window):,.2f}     "
            f"History {len(closes) / 252.0:,.1f}y     "
            f"Turnover {turnover / 1e7:,.1f} Cr     "
            f"Your money: {money}"))
        self._draw()

    def _failed(self, error) -> None:
        self.stats.config(text=f"{type(error).__name__}: {error}",
                          fg=Palette.bad)

    def _draw(self) -> None:
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
            features_module.build_panel())
        frame = production_module.scored(panel)
        _SCORES = {str(row.symbol): float(row.score)
                   for row in frame.itertuples()}
        return _SCORES

    def _score_done(self, scores) -> None:
        score = scores.get(self.symbol)
        if score is None:
            self.model.config(
                text="Model view: this name is not in the fitted universe, so "
                     "the model has no opinion on it.", fg=Palette.muted)
            return
        rising = score > 0
        self.colour = Palette.good if rising else Palette.bad
        self.model.config(
            text=(f"Model view: {'UP' if rising else 'DOWN'} "
                  f"(score {score:+.4f}) - the line is drawn in "
                  f"{'green' if rising else 'red'} to match."),
            fg=self.colour)
        self._draw()

    def _score_failed(self, error) -> None:
        self.model.config(
            text=f"Model view unavailable: {type(error).__name__}: {error}",
            fg=Palette.muted)
