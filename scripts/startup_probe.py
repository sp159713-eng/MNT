import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import gui
import theme


class Stub(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = config
        self.worker = gui.Worker(self)
        self.capital = 500000.0
        self.current = "Costs"
        self.f = theme.fonts()
        self.pages = {}

    def show(self, name):
        self.current = name


def main() -> None:
    root = Stub()
    root.withdraw()
    theme.style_widgets(root)
    container = tk.Frame(root)

    total = 0.0
    for name, factory in gui.App.PAGES:
        began = time.time()
        try:
            factory(container, root)
            took = time.time() - began
            total += took
            flag = "  <-- SLOW" if took > 1.0 else ""
            print(f"{name:<10} {took:7.2f}s{flag}")
        except Exception as error:
            print(f"{name:<10} FAILED {type(error).__name__}: {error}")
    print(f"{'TOTAL':<10} {total:7.2f}s")
    root.destroy()


if __name__ == "__main__":
    main()
