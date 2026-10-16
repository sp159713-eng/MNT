from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (CaptionLabel, PrimaryPushButton, PushButton,
                            SubtitleLabel)

from qtgui.common import (DataTable, EditSpinBox, TitledCard, bad_color,
                          confirm, set_tone, title_row)

RIGHT = Qt.AlignRight
LEFT = Qt.AlignLeft

COLUMNS = (("Side", 80, LEFT), ("Symbol", 120, LEFT), ("Qty", 80, RIGHT),
           ("Price", 110, RIGHT), ("Value", 120, RIGHT), ("Cost", 80, RIGHT),
           ("Reason", 160, LEFT))

NOTE = ("On a paper venue these fill against cached prices. On a live "
        "venue they are CNC limit orders, and every one is re-checked "
        "by risk.py immediately before it is sent - arming, per-order "
        "and daily limits, universe membership, and a 3% band against "
        "the live quote. The whole plan is pre-flighted first, so a "
        "rebalance that would breach a limit halfway is refused before "
        "any of it is placed.")


class OrdersPage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.plan = []
        self.setObjectName("page-Orders")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)

        top = title_row("Orders", self)
        plan_button = PushButton("Plan", self)
        plan_button.clicked.connect(self.build_plan)
        top.addWidget(plan_button)
        self.place_button = PrimaryPushButton("Place", self)
        self.place_button.clicked.connect(self.place)
        self.place_button.setEnabled(False)
        top.addWidget(self.place_button)
        root.addLayout(top)

        gate = TitledCard("Live trading", "disarmed by default", self)
        line = QHBoxLayout()
        line.setSpacing(8)
        self.gate_state = SubtitleLabel("", gate)
        line.addWidget(self.gate_state)
        line.addStretch(1)
        line.addWidget(CaptionLabel("minutes", gate))
        self.arm_minutes = EditSpinBox(gate)
        self.arm_minutes.setRange(1, 240)
        self.arm_minutes.setSingleStep(5)
        self.arm_minutes.setValue(15)
        self.arm_minutes.setFixedWidth(190)
        line.addWidget(self.arm_minutes)
        self.arm_button = PushButton("Arm", gate)
        self.arm_button.clicked.connect(self.toggle_arm)
        line.addWidget(self.arm_button)
        self.halt_button = PushButton("HALT", gate)
        self.halt_button.clicked.connect(self.toggle_halt)
        line.addWidget(self.halt_button)
        gate.body.addLayout(line)
        self.gate_detail = CaptionLabel("", gate)
        self.gate_detail.setWordWrap(True)
        gate.body.addWidget(self.gate_detail)
        root.addWidget(gate)

        self.status = CaptionLabel("", self)
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        card = TitledCard("Plan", "sells first, then buys", self)
        self.table = DataTable(COLUMNS, "No plan yet - press Plan", stretch=6,
                               parent=card)
        card.body.addWidget(self.table, 1)
        self.note = CaptionLabel("", card)
        self.note.setWordWrap(True)
        card.body.addWidget(self.note)
        root.addWidget(card, 1)

        self._gate_timer = QTimer(self)
        self._gate_timer.setSingleShot(True)
        self._gate_timer.timeout.connect(self.refresh_gate)

    def _say(self, text, tone):
        self.status.setText(text)
        set_tone(self.status, tone)

    def on_show(self):
        self.refresh_gate()

    def refresh_gate(self):
        import broker as broker_module
        import risk

        venue = broker_module.broker()
        live = getattr(venue, "live", False)
        state = risk.status()

        if state["halted"]:
            text, tone = "HALTED", "bad"
        elif not live:
            text, tone = f"PAPER ({venue.name})", "muted"
        elif state["armed"]:
            left = state["seconds_left"]
            text = f"ARMED  {left // 60}:{left % 60:02d}"
            tone = "bad"
        else:
            text, tone = "DISARMED", "good"

        self.gate_state.setText(text)
        set_tone(self.gate_state, tone)
        self.arm_button.setText("Disarm" if state["armed"] else "Arm")
        self.halt_button.setText("Resume" if state["halted"] else "HALT")

        if live and state["armed"]:
            self.place_button.setStyleSheet(
                "PrimaryPushButton{background:%s;border:1px solid %s;"
                "color:white;border-radius:5px;}" % (
                    bad_color().name(), bad_color().name()))
        else:
            self.place_button.setStyleSheet("")
        detail = (f"venue {venue.name} | today {state['orders_today']} orders, "
                  f"Rs {state['notional_today']:,.0f} | per order max "
                  f"Rs {state['max_order_value']:,.0f} | day max "
                  f"{state['max_day_orders']} orders")
        if not live:
            detail += "\nPaper venue - arming has no effect. Set MNT_BROKER=groww for live."
        self.gate_detail.setText(detail)
        self._gate_timer.start(1000)

    def toggle_arm(self):
        import risk

        if risk.armed():
            risk.disarm()
            self._say("disarmed", "muted")
        else:
            try:
                risk.arm(self.arm_minutes.value(), note="gui")
                self._say(
                    f"ARMED for {self.arm_minutes.value()} minutes - live "
                    f"orders will now be attempted. It lapses on its own.",
                    "bad")
            except risk.Refused as error:
                self._say(str(error), "bad")
        self.refresh_gate()

    def toggle_halt(self):
        import risk

        if risk.halted():
            risk.resume()
            self._say("resumed", "muted")
        else:
            risk.halt("gui")
            self._say("HALTED - nothing will be placed until resumed", "bad")
        self.refresh_gate()

    def build_plan(self):
        self._say("scoring the universe...", "muted")
        self.place_button.setEnabled(False)
        self.app.worker.submit(self._work, self._done, self._failed)

    def _work(self):
        import broker as broker_module
        import orders as orders_module

        targets = orders_module._model_picks()
        venue = broker_module.broker()
        return targets, orders_module.plan(targets, venue), venue.name

    def _done(self, payload):
        targets, plan, venue = payload
        self.plan = plan
        self.table.set_rows(
            [(o["side"], o["symbol"], o["qty"], f"{o['price']:,.2f}",
              f"{o['value']:,.0f}", f"{o['cost_bp']:.0f}bp",
              o.get("reason", "")) for o in plan],
            tones=["good" if o["side"] == "BUY" else "bad" for o in plan])
        cash = sum(o["value"] if o["side"] == "BUY" else -o["value"]
                   for o in plan)
        self._say(
            f"target book: {', '.join(targets)}   |   venue: {venue}   |   "
            f"{len(plan)} orders   |   net cash {-cash:+,.0f}", "muted")
        self.place_button.setEnabled(bool(plan))
        self.note.setText(NOTE)

    def _failed(self, error):
        self._say(str(error)[:110], "bad")

    def place(self):
        if not self.plan:
            return

        import broker as broker_module
        import risk

        venue = broker_module.broker()
        live = getattr(venue, "live", False)

        if live and not risk.armed():
            self._say(f"{venue.name} is a LIVE venue and the gate is "
                      f"disarmed. Arm it above to place real orders.", "bad")
            return

        if live:
            total = sum(o["value"] for o in self.plan)
            if not confirm(
                    self, "Place REAL orders?",
                    f"This will place {len(self.plan)} orders on {venue.name} "
                    f"for about Rs {total:,.0f} of REAL money.\n\n"
                    f"The strategy's walk-forward t-statistic is below 2 and "
                    f"the order-level sim lost to buy-and-hold.\n\n"
                    f"Continue?", danger=True, default_yes=False):
                self._say("cancelled", "muted")
                return

        self.place_button.setEnabled(False)
        self._say("placing...", "muted")

        def work():
            import orders as orders_module
            return orders_module.execute(self.plan, venue, confirm_live=live)

        self.app.worker.submit(work, self._placed, self._failed)

    def _placed(self, results):
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
        self._say(message, "bad" if failed else
                  ("warn" if partial else "good"))
        self.plan = []

        if failed or partial or skipped:
            self.place_button.setEnabled(True)
        else:
            self.build_plan()
