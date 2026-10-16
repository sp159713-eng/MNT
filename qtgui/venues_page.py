from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, LineEdit, PasswordLineEdit,
                            PrimaryPushButton, PushButton)

from qtgui.common import TitledCard, set_tone, title_row

SECRET_WORDS = ("key", "secret", "token", "pin", "password", "totp")


def is_secret(field):
    return any(word in field.lower() for word in SECRET_WORDS)


def hide_on_edit(entry):
    entry.textEdited.connect(lambda _t: entry.setEchoMode(LineEdit.Password))
from qtgui.settings_page import note, scrolled

POLICY = (
    "Saving broker credentials does NOT enable live trading. The live "
    "adapters raise NotImplementedError on every method that would touch an "
    "account, and orders.execute refuses any live venue unless "
    "confirm_live=True is passed in code - which this window never does.\n\n"
    "That is deliberate. The walk-forward t-statistic is below 2, the "
    "order-level sim lost to buy-and-hold, and charges alone consumed about "
    "2% of capital in a year.\n\n"
    "Keys are stored as plain JSON at {path}, outside the project folder, "
    "readable by anyone with your Windows login. Environment variables "
    "override saved values.")


class VenuePage(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        import credentials

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        top = title_row("API keys", self)
        self.status = CaptionLabel("", self)
        top.addWidget(self.status)
        root.addLayout(top)
        scroll, body = scrolled(self)
        root.addWidget(scroll, 1)

        self.entries = {}
        self.headers = {}
        for name, spec in credentials.PROVIDERS.items():
            card = TitledCard(spec["title"],
                              "broker" if spec["kind"] == "broker" else "model",
                              self)
            state = note("", self)
            card.body.addWidget(state)
            self.headers[name] = state
            for field, label, variable, help_text in spec["fields"]:
                line = QHBoxLayout()
                line.setSpacing(10)
                caption = BodyLabel(label, self)
                caption.setFixedWidth(130)
                line.addWidget(caption)
                if is_secret(field):
                    entry = PasswordLineEdit(self)
                    hide_on_edit(entry)
                else:
                    entry = LineEdit(self)
                line.addWidget(entry, 1)
                env = CaptionLabel(variable, self)
                env.setFixedWidth(200)
                line.addWidget(env)
                card.body.addLayout(line)
                self.entries[(name, field)] = entry
                card.body.addWidget(note(f"    {help_text}", self))
            buttons = QHBoxLayout()
            save = PrimaryPushButton("Save", self)
            save.clicked.connect(lambda _=False, n=name: self.save(n))
            forget = PushButton("Forget", self)
            forget.clicked.connect(lambda _=False, n=name: self.forget(n))
            buttons.addWidget(save)
            buttons.addWidget(forget)
            buttons.addStretch(1)
            card.body.addLayout(buttons)
            body.addWidget(card)

        policy = TitledCard("Why ordering stays on paper", "", self)
        policy.body.addWidget(
            note(POLICY.format(path=credentials.store_path()), self))
        body.addWidget(policy)
        body.addStretch(1)

    def on_show(self):
        import credentials

        for name, spec in credentials.PROVIDERS.items():
            values = credentials.effective(name)
            ready = spec["usable"](values)
            header = self.headers[name]
            header.setText(("READY - " if ready else "")
                           + spec["explain"](values))
            set_tone(header, "good" if ready else "muted")
            for field, _, _, _ in spec["fields"]:
                entry = self.entries[(name, field)]
                where = credentials.source(name, field)
                entry.setEnabled(True)
                entry.clear()
                if is_secret(field):
                    entry.setEchoMode(LineEdit.Password)
                if where == "environment":
                    entry.setEchoMode(LineEdit.Normal)
                    entry.setText(f"{credentials.masked(values[field])}  "
                                  f"(from environment)")
                    entry.setEnabled(False)
                elif where == "saved":
                    entry.setEchoMode(LineEdit.Normal)
                    entry.setText(credentials.masked(values[field]))

    def save(self, name):
        import credentials

        updates = {}
        for field, _, _, _ in credentials.provider(name)["fields"]:
            entry = self.entries[(name, field)]
            if not entry.isEnabled():
                continue
            typed = entry.text().strip()
            if not typed or set(typed) <= {"*"} or "*" * 8 in typed:
                continue
            updates[field] = typed
        if not updates:
            self.status.setText("nothing changed")
            set_tone(self.status, "muted")
            return
        path = credentials.save(name, updates)
        credentials.apply_to_environment(name)
        self.status.setText(f"saved {len(updates)} field(s) to {path}")
        set_tone(self.status, "good")
        self.on_show()

    def forget(self, name):
        import credentials

        removed = credentials.forget(name)
        self.status.setText(f"{name}: removed" if removed
                            else f"{name}: nothing stored")
        set_tone(self.status, "muted")
        self.on_show()
