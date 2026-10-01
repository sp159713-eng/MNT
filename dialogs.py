from __future__ import annotations

import tkinter as tk

from theme import SPACE, Button, Palette, fonts


def frame_window(window: tk.Toplevel, title: str, on_close=None) -> tk.Frame:
    window.overrideredirect(True)
    window.configure(bg=Palette.border)
    f = fonts()
    shell = tk.Frame(window, bg=Palette.bg)
    shell.pack(fill="both", expand=True, padx=1, pady=1)

    close = on_close or window.destroy
    head = tk.Frame(shell, bg=Palette.bg)
    head.pack(fill="x")
    label = tk.Label(head, text=title, bg=Palette.bg, fg=Palette.text,
                     font=f["h2"], anchor="w")
    label.pack(side="left", padx=(SPACE["lg"], 0), pady=SPACE["md"])
    x = tk.Label(head, text="×", bg=Palette.bg, fg=Palette.muted,
                 font=f["h1"], padx=SPACE["md"], cursor="hand2")
    x.pack(side="right")
    x.bind("<Button-1>", lambda _e: close())
    x.bind("<Enter>", lambda _e: x.config(bg=Palette.bad, fg="#ffffff"))
    x.bind("<Leave>", lambda _e: x.config(bg=Palette.bg, fg=Palette.muted))
    tk.Frame(shell, bg=Palette.border, height=1).pack(fill="x")

    drag = {}

    def press(event):
        drag["x"], drag["y"] = event.x_root, event.y_root
        drag["wx"], drag["wy"] = window.winfo_x(), window.winfo_y()

    def move(event):
        if drag:
            window.geometry(f"+{drag['wx'] + event.x_root - drag['x']}"
                            f"+{drag['wy'] + event.y_root - drag['y']}")

    for grip in (head, label):
        grip.bind("<ButtonPress-1>", press)
        grip.bind("<B1-Motion>", move)

    window.bind("<Escape>", lambda _e: close())
    body = tk.Frame(shell, bg=Palette.bg)
    body.pack(fill="both", expand=True, padx=SPACE["lg"], pady=SPACE["lg"])
    return body


def centre(window: tk.Toplevel, parent: tk.Misc, width: int, height: int) -> None:
    window.update_idletasks()
    top = parent.winfo_toplevel()
    x = top.winfo_rootx() + (top.winfo_width() - width) // 2
    y = top.winfo_rooty() + (top.winfo_height() - height) // 3
    window.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")


def confirm(parent: tk.Misc, title: str, message: str, default_yes: bool = True,
            yes: str = "Yes", no: str = "No", danger: bool = False) -> bool:
    window = tk.Toplevel(parent.winfo_toplevel())
    result = {"value": False}

    def finish(value):
        result["value"] = value
        window.destroy()

    body = frame_window(window, title, on_close=lambda: finish(False))
    f = fonts()
    tk.Label(body, text=message, bg=Palette.bg, fg=Palette.muted,
             font=f["body"], wraplength=420, justify="left",
             anchor="w").pack(fill="x")
    row = tk.Frame(body, bg=Palette.bg)
    row.pack(fill="x", pady=(SPACE["lg"], 0))
    Button(row, yes, lambda: finish(True),
           kind="danger" if danger else "primary").pack(side="right")
    Button(row, no, lambda: finish(False), kind="ghost").pack(
        side="right", padx=(0, SPACE["sm"]))
    window.bind("<Return>", lambda _e: finish(default_yes))
    centre(window, parent, 500, window.winfo_reqheight())
    window.update_idletasks()
    centre(window, parent, 500, window.winfo_reqheight())
    window.transient(parent.winfo_toplevel())
    window.focus_force()
    try:
        window.grab_set()
    except tk.TclError:
        pass
    window.wait_window()
    return result["value"]
