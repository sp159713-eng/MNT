from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, PushButton

from qtgui.common import (DataTable, StatCard, TitledCard, ask_amount,
                          set_tone, title_row)

RIGHT = Qt.AlignRight
LEFT = Qt.AlignLeft

TILES = (("bank", "bank"), ("equity", "pool"), ("cash", "cash"),
         ("pnl", "P&L"), ("trades", "fills"))

POSITION_COLUMNS = (("Symbol", 110, LEFT), ("Qty", 70, RIGHT),
                    ("Avg", 100, RIGHT), ("Last", 100, RIGHT),
                    ("P&L", 100, RIGHT))

FILL_COLUMNS = (("Time", 90, LEFT), ("Side", 70, RIGHT),
                ("Symbol", 110, LEFT), ("Qty", 70, RIGHT),
                ("Price", 100, RIGHT))


class AccountPage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setObjectName("page-Account")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)

        top = title_row("Paper account", self)
        self.status = CaptionLabel("", self)
        top.addWidget(self.status)
        for text, handler in (("Set bank", self.set_bank),
                              ("Put in pool", self.put_in),
                              ("Take out", self.take_out),
                              ("Refresh", self.refresh)):
            button = PushButton(text, self)
            button.clicked.connect(handler)
            top.addWidget(button)
        self.reset_button = PushButton("Reset", self)
        self.reset_button.clicked.connect(self.reset)
        top.addWidget(self.reset_button)
        root.addLayout(top)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.tiles = {}
        for key, label in TILES:
            tile = StatCard(label, self)
            stats.addWidget(tile, 1)
            self.tiles[key] = tile.value
        root.addLayout(stats)

        panes = QHBoxLayout()
        panes.setSpacing(12)
        left = TitledCard("Positions", "", self)
        self.positions = DataTable(POSITION_COLUMNS, "No open positions",
                                   stretch=0, parent=left)
        left.body.addWidget(self.positions, 1)
        panes.addWidget(left, 1)
        right = TitledCard("Recent fills", "", self)
        self.fills = DataTable(FILL_COLUMNS, "No fills yet", stretch=2,
                               parent=right)
        right.body.addWidget(self.fills, 1)
        panes.addWidget(right, 1)
        root.addLayout(panes, 1)

    def on_show(self):
        self.refresh()

    def reset(self):
        import broker as broker_module

        broker_module.broker("paper").reset(self.app.capital)
        self.refresh()

    def _bank(self):
        import theme as theme_module

        try:
            return max(float(theme_module.preferences().get("bank", 0.0)), 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _fail(self, text):
        self.status.setText(text)
        set_tone(self.status, "bad")

    def _finish(self, text):
        self.status.setText(text)
        set_tone(self.status, "muted")
        self.refresh()

    def set_bank(self):
        import theme as theme_module

        amount = ask_amount(self, "Set bank", "Bank balance in Rs.",
                            f"{self._bank():,.0f}")
        if amount is None:
            return
        if amount < 0:
            self._fail("Bank balance cannot be negative.")
            return
        theme_module.save_key("bank", amount)
        self._finish(f"bank set to {amount:,.0f}")

    def put_in(self):
        import broker as broker_module
        import theme as theme_module

        amount = ask_amount(self, "Put in pool",
                            "Rs to move from the bank into the pool.")
        if amount is None:
            return
        bank = self._bank()
        if amount <= 0:
            self._fail("Amount must be above zero.")
            return
        if amount > bank:
            self._fail(f"Bank holds only {bank:,.0f}.")
            return
        account = broker_module.broker("paper")
        if theme_module.preferences().get("bot_funded"):
            account.state["cash"] = float(account.state["cash"]) + amount
            account.state["opened"] = float(
                account.state.get("opened", 0.0)) + amount
            account.save()
        else:
            account.reset(amount)
        theme_module._write(dict(theme_module.preferences(),
                                 bank=bank - amount, bot_funded=True))
        self.app.capital = float(account.state["opened"])
        self._finish(f"{amount:,.0f} moved into the pool")

    def take_out(self):
        import broker as broker_module
        import theme as theme_module

        amount = ask_amount(self, "Take out",
                            "Rs to move from the pool's cash back to the bank.")
        if amount is None:
            return
        account = broker_module.broker("paper")
        if amount <= 0:
            self._fail("Amount must be above zero.")
            return
        if amount > account.cash():
            self._fail(f"Pool cash is only {account.cash():,.0f}.")
            return
        account.state["cash"] = float(account.state["cash"]) - amount
        account.state["opened"] = max(
            float(account.state.get("opened", 0.0)) - amount, 0.0)
        account.save()
        theme_module.save_key("bank", self._bank() + amount)
        if theme_module.preferences().get("bot_funded"):
            self.app.capital = float(account.state["opened"])
        self._finish(f"{amount:,.0f} moved to the bank")

    def refresh(self):
        self.app.worker.submit(self._work, self._done, lambda e: None)

    def _work(self):
        import broker as broker_module

        account = broker_module.broker("paper")
        equity, unrealised = account.value()
        rows = []
        for symbol, position in account.holdings().items():
            try:
                last = account.ltp(symbol)
            except Exception:
                last = position["avg"]
            rows.append((symbol, position["qty"], f"{position['avg']:,.2f}",
                         f"{last:,.2f}",
                         f"{(last - position['avg']) * position['qty']:+,.0f}"))
        return {"bank": self._bank(), "equity": equity, "cash": account.cash(),
                "opened": account.state.get("opened", equity),
                "positions": rows, "trades": account.state["trades"][-14:],
                "count": len(account.state["trades"])}

    def _done(self, result):
        pnl = result["equity"] - result["opened"]
        self.tiles["bank"].setText(f"{result['bank']:,.0f}")
        self.tiles["equity"].setText(f"{result['equity']:,.0f}")
        self.tiles["cash"].setText(f"{result['cash']:,.0f}")
        self.tiles["pnl"].setText(f"{pnl:+,.0f}")
        set_tone(self.tiles["pnl"], "good" if pnl >= 0 else "bad")
        self.tiles["trades"].setText(str(result["count"]))

        self.positions.set_rows(result["positions"])
        self.fills.set_rows([
            (trade["time"][11:19], trade["side"], trade["symbol"],
             trade["qty"], f"{trade['price']:,.2f}")
            for trade in reversed(result["trades"])])
