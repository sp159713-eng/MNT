from __future__ import annotations

import tkinter as tk

from theme import RADIUS, SPACE, Palette, fonts, round_rect


class SidebarItem(tk.Canvas):
    HEIGHT = 34

    def __init__(self, parent, text, command=None):
        super().__init__(parent, height=self.HEIGHT, highlightthickness=0,
                         bd=0, bg=Palette.sidebar_bg, cursor="hand2")
        self.text = text
        self.command = command
        self.active = False
        self.hover = False
        self.font = fonts()["body"]
        self.bind("<Enter>", lambda _e: self._set("hover", True))
        self.bind("<Leave>", lambda _e: self._set("hover", False))
        self.bind("<Button-1>", lambda _e: self.command and self.command())
        self.bind("<Configure>", lambda _e: self._draw())

    def _set(self, key, value) -> None:
        setattr(self, key, value)
        self._draw()

    def set_active(self, active: bool) -> None:
        self._set("active", active)

    def _draw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 2)
        fill = (Palette.sidebar_active if self.active else
                Palette.sidebar_hover if self.hover else "")
        if fill:
            round_rect(self, 0.5, 0.5, width - 1.5, self.HEIGHT - 1.5,
                       RADIUS["md"], fill, fill)
        if self.active:
            self.create_rectangle(0, 9, 3, self.HEIGHT - 9, fill=Palette.accent,
                                  outline="")
        self.create_text(SPACE["lg"], self.HEIGHT / 2, text=self.text,
                         anchor="w", font=self.font,
                         fill=Palette.text if (self.active or self.hover)
                         else Palette.muted)


def section_label(parent, text: str) -> tk.Label:
    label = tk.Label(parent, text=text.upper(), bg=Palette.sidebar_bg,
                     fg=Palette.faint, font=fonts()["label"], anchor="w")
    label.pack(fill="x", padx=SPACE["lg"], pady=(SPACE["lg"], SPACE["xs"]))
    return label


class TabIsland(tk.Canvas):
    FILL = "#0b0b0f"
    EDGE = "#242430"
    KNOB = "#2c2c32"
    ON = "#f2f2f5"
    OFF = "#9a9aa5"
    HEIGHT = 36
    PAD = 4
    STEPS = 12

    def __init__(self, parent, labels, command):
        self.font = fonts()["label"]
        self.labels = list(labels)
        self.command = command
        self.spans, x = [], self.PAD
        for label in self.labels:
            w = self.font.measure(label) + SPACE["xl"] + SPACE["sm"]
            self.spans.append((x, x + w))
            x += w
        self.width = x + self.PAD
        super().__init__(parent, width=self.width, height=self.HEIGHT,
                         highlightthickness=0, bd=0, bg=parent.cget("bg"),
                         cursor="hand2")
        self.active = 0
        self.hover = None
        self.knob = list(self.spans[0])
        self.job = None
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._hover(None))
        self.bind("<Button-1>", self._click)
        self._draw()

    def _index_at(self, x):
        for i, (a, b) in enumerate(self.spans):
            if a <= x < b:
                return i
        return None

    def _motion(self, event):
        self._hover(self._index_at(event.x))

    def _hover(self, index):
        if index != self.hover:
            self.hover = index
            self._draw()

    def _click(self, event):
        index = self._index_at(event.x)
        if index is not None and index != self.active:
            self.command(self.labels[index])

    def set_active(self, label, animate=True):
        index = self.labels.index(label)
        self.active = index
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        if not animate:
            self.knob = list(self.spans[index])
            self._draw()
            return
        start, end = tuple(self.knob), self.spans[index]
        self._step(start, end, 1)

    def _step(self, start, end, n):
        t = n / self.STEPS
        e = 1 - (1 - t) ** 3
        self.knob = [start[0] + (end[0] - start[0]) * e,
                     start[1] + (end[1] - start[1]) * e]
        self._draw()
        self.job = self.after(16, self._step, start, end, n + 1) if n < self.STEPS else None

    def _draw(self):
        self.delete("all")
        h = self.HEIGHT
        round_rect(self, 0.5, 0.5, self.width - 1.5, h - 1.5, h / 2 - 1,
                   self.FILL, self.EDGE)
        a, b = self.knob
        round_rect(self, a, self.PAD, b, h - self.PAD - 1, (h - 2 * self.PAD) / 2 - 1,
                   self.KNOB, self.KNOB, tag="knob")
        for i, (label, (x0, x1)) in enumerate(zip(self.labels, self.spans)):
            fg = self.ON if i == self.active or i == self.hover else self.OFF
            self.create_text((x0 + x1) / 2, h / 2, text=label, font=self.font, fill=fg)


class TabbedPage(tk.Frame):
    embeds_pages = True

    def __init__(self, parent, app, title: str, tabs):
        super().__init__(parent, bg=Palette.bg)
        self.app = app
        f = fonts()
        head = tk.Frame(self, bg=Palette.bg)
        head.pack(fill="x", pady=(0, SPACE["md"]))
        tk.Label(head, text=title, bg=Palette.bg, fg=Palette.text,
                 font=f["h1"]).pack(side="left")
        self.content = tk.Frame(self, bg=Palette.bg)
        self.content.embeds_pages = True
        self.content.pack(fill="both", expand=True)
        self.tabs = {}
        self.active = None
        for label, factory in tabs:
            self.tabs[label] = factory(self.content, app)
        self.island = TabIsland(head, self.tabs, self.select)
        self.island.place(relx=0.5, rely=0.5, anchor="center")
        self.select(next(iter(self.tabs)), notify=False)

    def select(self, name: str, notify: bool = True) -> None:
        for page in self.tabs.values():
            page.pack_forget()
        self.island.set_active(name, animate=self.active is not None)
        self.active = name
        self.tabs[name].pack(fill="both", expand=True)
        if notify:
            self.tabs[name].on_show()

    def on_show(self) -> None:
        self.tabs[self.active].on_show()
