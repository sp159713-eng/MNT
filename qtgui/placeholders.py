from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, SegmentedWidget, SubtitleLabel

NOT_PORTED = "Not ported yet - use the classic app"


class PlaceholderView(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(SubtitleLabel(title, self))
        layout.addWidget(CaptionLabel(NOT_PORTED, self))
        layout.addStretch(1)


class PlaceholderPage(QWidget):
    def __init__(self, title, tabs=(), parent=None):
        super().__init__(parent)
        self.setObjectName(f"page-{title}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)
        if not tabs:
            layout.addWidget(PlaceholderView(title, self))
            return
        self.pivot = SegmentedWidget(self)
        self.stack = QStackedWidget(self)
        for name in tabs:
            view = PlaceholderView(name, self)
            view.setObjectName(f"view-{title}-{name}")
            self.stack.addWidget(view)
            self.pivot.addItem(
                routeKey=view.objectName(), text=name,
                onClick=lambda _=False, v=view: self.stack.setCurrentWidget(v))
        self.pivot.setCurrentItem(self.stack.widget(0).objectName())
        layout.addWidget(self.pivot)
        layout.addWidget(self.stack, 1)
