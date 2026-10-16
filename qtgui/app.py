import os
import sys

from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication
from qfluentwidgets import (FluentIcon, FluentWindow, NavigationItemPosition,
                            Theme, isDarkTheme, setTheme, setThemeColor,
                            themeColor)

import auth as auth_module
import theme as theme_module
from qtgui.account_page import AccountPage
from qtgui.backtest_page import BacktestSection
from qtgui.book_page import BookPage
from qtgui.bots_page import BotsPage
from qtgui.news_page import NewsSection
from qtgui.orders_page import OrdersPage
from qtgui.placeholders import PlaceholderPage
from qtgui.signals_page import SignalsPage
from qtgui.settings_page import SettingsSection
from qtgui.sim_page import SimPage
from qtgui.stock_page import UniversePage
from qtgui.training_page import TrainingPage
from qtgui.worker import Worker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECTIONS = (
    ("Trade", (("Book", FluentIcon.BOOK_SHELF, ()),
               ("Orders", FluentIcon.SHOPPING_CART, ()),
               ("Account", FluentIcon.PEOPLE, ()),
               ("Signals", FluentIcon.SPEED_HIGH, ()),
               ("News", FluentIcon.DOCUMENT, ("News", "Pulse")),
               ("Stock", FluentIcon.MARKET, ()))),
    ("Results", (("Sim", FluentIcon.PLAY, ()),
                 ("Backtest", FluentIcon.HISTORY, ("Backtest", "Costs")),
                 ("Bots", FluentIcon.ROBOT, ()),
                 ("Training", FluentIcon.EDUCATION, ()))),
)

REAL_PAGES = {"Book": BookPage, "Orders": OrdersPage,
              "Account": AccountPage, "Signals": SignalsPage,
              "Stock": UniversePage, "Sim": SimPage,
              "News": NewsSection, "Backtest": BacktestSection,
              "Bots": BotsPage, "Training": TrainingPage}

ADMIN_ONLY = ("Bots", "Training")


def initial_capital():
    capital = 500000.0
    try:
        if theme_module.preferences().get("bot_funded"):
            import broker as broker_module

            capital = float(broker_module.broker("paper").state["opened"])
    except Exception:
        pass
    return capital


def apply_theme(name):
    setTheme(Theme.LIGHT if name == "light" else Theme.DARK)
    accent = QColor(theme_module.Dark.accent)
    setThemeColor(accent.darker(135) if name == "light" else accent)
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(primary_sheet(name == "light"))


def primary_sheet(light):
    fill = themeColor().name()
    text, off_text, off_fill = (
        ("white", "rgba(255, 255, 255, 0.9)", "rgb(205, 205, 205)") if light
        else ("black", "rgba(255, 255, 255, 0.43)", "rgb(52, 52, 52)"))
    return ("PrimaryPushButton{color:%s;background-color:%s;"
            "border:1px solid %s;border-radius:5px;padding:5px 12px 6px 12px;}"
            "PrimaryPushButton:disabled{color:%s;background-color:%s;"
            "border:1px solid %s;}" % (text, fill, fill, off_text, off_fill,
                                       off_fill))


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ascent")
        icon_path = os.path.join(BASE_DIR, "mnt.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1160, 740)
        self.setMinimumSize(960, 640)
        self.worker = Worker(self)
        self.capital = initial_capital()
        self.pages = {}
        self._build()
        self.stackedWidget.currentChanged.connect(self._page_changed)

    def _build(self):
        nav = self.navigationInterface
        for heading, entries in SECTIONS:
            entries = [e for e in entries if e[0] not in ADMIN_ONLY
                       or auth_module.is_admin()]
            if not entries:
                continue
            nav.addItemHeader(heading)
            for title, icon, tabs in entries:
                if title in REAL_PAGES:
                    page = REAL_PAGES[title](self)
                else:
                    page = PlaceholderPage(title, tabs, self)
                self.pages[title] = page
                self.addSubInterface(page, icon, title)
        nav.addItem("theme", self._theme_icon(), self._theme_text(),
                    onClick=self.toggle_theme, selectable=False,
                    position=NavigationItemPosition.BOTTOM)
        settings = SettingsSection(self)
        self.pages["Settings"] = settings
        self.addSubInterface(settings, FluentIcon.SETTING, "Settings",
                             NavigationItemPosition.BOTTOM)

    def _page_changed(self, _index):
        handler = getattr(self.stackedWidget.currentWidget(), "on_show", None)
        if handler is not None:
            handler()

    def _theme_icon(self):
        return FluentIcon.BRIGHTNESS if isDarkTheme() else FluentIcon.CONSTRACT

    def _theme_text(self):
        return "Light theme" if isDarkTheme() else "Dark theme"

    def toggle_theme(self):
        name = "light" if isDarkTheme() else "dark"
        apply_theme(name)
        theme_module.save_theme(name)
        item = self.navigationInterface.widget("theme")
        item.setIcon(self._theme_icon())
        item.setText(self._theme_text())


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(theme_module.saved_theme())
    window = MainWindow()
    window.show()
    return app.exec()
