from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (CaptionLabel, CardWidget, PrimaryPushButton,
                            PushButton, SegmentedWidget, StrongBodyLabel)

from qtgui.common import DataTable, TitledCard, set_tone, title_row

RIGHT = Qt.AlignRight
LEFT = Qt.AlignLeft
CENTER = Qt.AlignHCenter

TODO_COLUMNS = (("Side", 80, CENTER), ("Symbol", 130, LEFT),
                ("Qty", 90, RIGHT), ("Price", 110, RIGHT),
                ("Value", 130, RIGHT), ("Cost bp", 90, RIGHT),
                ("Reason", 160, LEFT))

SCREEN_COLUMNS = (("Symbol", 130, LEFT), ("Price", 100, RIGHT),
                  ("Turnover", 120, RIGHT), ("Screen", 90, CENTER),
                  ("Why", 420, LEFT))


class SignalsPage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.view = "todo"
        self.scope_name = "book"
        self.setObjectName("page-Signals")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(12)

        top = title_row("Signals", self)
        self.update_button = PushButton("Update ASM/GSM", self)
        self.update_button.clicked.connect(self.update_surveillance)
        top.addWidget(self.update_button)
        self.run_button = PrimaryPushButton("Refresh", self)
        self.run_button.clicked.connect(self.refresh)
        top.addWidget(self.run_button)
        root.addLayout(top)

        switch = QHBoxLayout()
        self.segments = SegmentedWidget(self)
        self.segments.addItem("todo", "What to do",
                              lambda: self.show_view("todo"))
        self.segments.addItem("book", "Screen: my book", self.run_book)
        self.segments.addItem("candidates", "Screen: other names",
                              self.run_candidates)
        switch.addWidget(self.segments)
        switch.addStretch(1)
        root.addLayout(switch)

        strip = CardWidget(self)
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(14, 8, 14, 8)
        self.gate = StrongBodyLabel("", strip)
        strip_layout.addWidget(self.gate)
        root.addWidget(strip)

        self.source = CaptionLabel("", self)
        self.source.setWordWrap(True)
        set_tone(self.source, "warn")
        root.addWidget(self.source)

        self.todo_card = TitledCard(
            "What to do", "model book differenced against the account", self)
        self.todo_table = DataTable(
            TODO_COLUMNS, "No orders to show - press Refresh", stretch=6,
            parent=self.todo_card)
        self.todo_card.body.addWidget(self.todo_table, 1)
        self.todo_summary = CaptionLabel("", self.todo_card)
        self.todo_summary.setWordWrap(True)
        self.todo_card.body.addWidget(self.todo_summary)
        root.addWidget(self.todo_card, 1)

        self.card = TitledCard(
            "Names", "liquidity, pattern flags, surveillance", self)
        self.table = DataTable(
            SCREEN_COLUMNS, "Nothing screened yet - press Refresh",
            stretch=4, parent=self.card)
        self.card.body.addWidget(self.table, 1)
        self.summary = CaptionLabel("", self.card)
        self.summary.setWordWrap(True)
        self.card.body.addWidget(self.summary)
        root.addWidget(self.card, 1)

        self.show_view("todo")

    def show_view(self, which):
        self.view = which
        self._mark_scope()
        is_todo = which == "todo"
        self.todo_card.setVisible(is_todo)
        self.card.setVisible(not is_todo)
        self.source.setVisible(not is_todo)

    def _mark_scope(self):
        active = "todo" if self.view == "todo" else self.scope_name
        self.segments.setCurrentItem(active)

    def refresh(self):
        if self.view == "screen":
            self._run(lambda m: getattr(m, self.scope_name)(), self.scope_name)
            return

        self.run_button.setEnabled(False)
        self.todo_summary.setText("Scoring the universe and reading the "
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

    def _todo_failed(self, error):
        self.run_button.setEnabled(True)
        self.todo_summary.setText(f"{type(error).__name__}: {error}")

    def _todo_done(self, payload):
        picks, plan, equity = payload
        self.run_button.setEnabled(True)
        self.todo_table.set_rows(
            [(o["side"], o["symbol"], f"{o['qty']:,}",
              f"{o.get('price', 0.0):,.2f}", f"{o.get('value', 0.0):,.0f}",
              f"{o.get('cost_bp', 0.0):,.1f}", o.get("reason", ""))
             for o in plan],
            bands=["good" if o["side"] == "BUY" else "bad" for o in plan])

        traded = sum(o.get("value", 0.0) for o in plan)
        buys = sum(1 for o in plan if o["side"] == "BUY")
        self.todo_summary.setText(
            f"{buys} to buy, {len(plan) - buys} to sell, "
            f"{traded:,.0f} traded against {equity:,.0f} equity.  "
            f"Book: {', '.join(picks)}.  Read-only.")
        self._refresh_gate()

    def _refresh_gate(self):
        import risk

        armed, halted = risk.armed(), risk.halted()
        state = "ARMED" if armed else "closed"
        if armed:
            state += f" ({risk.arm_remaining()}m left)"
        self.gate.setText(
            f"live gate {state}" + ("   HALTED" if halted else ""))
        set_tone(self.gate, "bad" if (armed or halted) else "muted")

    def on_show(self):
        import screen as screen_module

        self.source.setText(screen_module.describe_source())
        self._refresh_gate()
        if self.view == "screen" and self.table.row_count() == 0:
            self.run_book()
        elif self.view == "todo" and self.todo_table.row_count() == 0:
            self.refresh()

    def run_book(self):
        self.scope_name = "book"
        self.show_view("screen")
        self._run(lambda m: m.book(), "book")

    def run_candidates(self):
        self.scope_name = "candidates"
        self.show_view("screen")
        self._run(lambda m: m.candidates(), "candidates")

    def _run(self, pick, what):
        self.run_button.setEnabled(False)
        self.summary.setText(
            f"Screening the {what}. Names without a cached history need a "
            f"fetch, so the first run is slow.")

        def work():
            import screen as screen_module

            return what, pick(screen_module), screen_module.describe_source()

        self.app.worker.submit(work, self._done, self._failed)

    def update_surveillance(self):
        self.update_button.setEnabled(False)
        self.summary.setText("Asking NSE for the ASM and GSM lists...")

        def work():
            import screen as screen_module

            return (screen_module.fetch_surveillance(),
                    screen_module.describe_source())

        self.app.worker.submit(work, self._updated, self._update_failed)

    def _update_failed(self, error):
        self.update_button.setEnabled(True)
        self.summary.setText(f"Update failed: {type(error).__name__}: "
                             f"{error}  The previous list is untouched.")

    def _updated(self, payload):
        result, source = payload
        self.update_button.setEnabled(True)
        self.source.setText(source)
        if result["ok"]:
            self.summary.setText(
                f"ASM/GSM updated: {result['rows']} symbols. Screen again "
                f"- the rows above were judged against the previous list.")
        else:
            self.summary.setText(
                f"Update failed: {result['error']}  The previous list, if "
                f"any, is untouched.")

    def _failed(self, error):
        self.run_button.setEnabled(True)
        self.summary.setText(f"{type(error).__name__}: {error}")

    def _done(self, payload):
        what, rows, source = payload
        self.run_button.setEnabled(True)
        self.source.setText(source)
        tones = []
        values = []
        for row in rows:
            tone = "bad" if row["listed"] else {
                "avoid": "bad", "caution": "warn"}.get(row["severity"])
            tones.append(tone)
            turnover = ("unknown" if row["turnover"] is None
                        else f"{row['turnover'] / 1e7:,.1f} Cr")
            values.append((row["symbol"], f"{row['price']:,.2f}", turnover,
                           row["severity"],
                           row["verdict"] if row["listed"] else row["flags"]))
        self.table.set_rows(values, tones=tones)

        flagged = sum(1 for r in rows if r["severity"] != "clean")
        listed = sum(1 for r in rows if r["listed"])
        if not rows:
            import config

            self.summary.setText(
                "No stocks yet - add them on the Stock page."
                if not config.UNIVERSE
                else f"{what}: nothing to screen.")
            return
        self.summary.setText(
            f"{what}: {len(rows)} screened, {flagged} with flags, "
            f"{listed} on a surveillance list.  Clean means no flag "
            f"tripped, not cleared.")
