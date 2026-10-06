"""Windows glue: foreground app, clipboard paste, tkinter overlay."""
import logging, queue, time, tkinter as tk
import keyboard, pyperclip, win32api, win32gui, win32process


def active_app():
    """(lowercase process exe name, window title) of the foreground window."""
    hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(hwnd)
    try:
        h = win32api.OpenProcess(0x0410, False, win32process.GetWindowThreadProcessId(hwnd)[1])
        exe = win32process.GetModuleFileNameEx(h, 0).rsplit("\\", 1)[-1].lower()
        win32api.CloseHandle(h)
    except Exception:
        exe = ""
    return exe, title


def paste(text):
    # ponytail: text-only clipboard restore; images/files on the clipboard are lost
    old = pyperclip.paste()
    pyperclip.copy(text)
    keyboard.send("ctrl+v")
    time.sleep(0.4)  # slow targets read the clipboard late
    pyperclip.copy(old)


def insert(text, cfg):
    """Output sink: cfg["output"]["method"] = "paste" (default) or "type" (keyboard.write, for paste-blocking apps)."""
    if cfg.get("output", {}).get("method", "paste") == "type":
        for i, line in enumerate(text.split("\n")):  # a bare newline would send (Enter) in chat apps
            if i:
                keyboard.send("shift+enter")
            keyboard.write(line)
    else:
        paste(text)


def copy_selection():
    """Selected text via ctrl+c, or None if nothing was copied."""
    old = pyperclip.paste()
    pyperclip.copy("")
    keyboard.send("ctrl+c")
    time.sleep(0.3)
    got = pyperclip.paste()
    pyperclip.copy(old)
    return got or None


class Overlay:
    """Non-activating HUD. Owns the tk root: run() on the main thread, post() from others.

    Never withdrawn/deiconified after startup (that steals focus); shown by toggling alpha.
    """

    W = 700

    def __init__(self):
        prev = win32gui.GetForegroundWindow()  # tk init steals focus; give it back below
        self.q = queue.Queue()
        self.level, self.recording = 0.0, False  # level is written by the audio thread
        self.root = r = tk.Tk()
        r.overrideredirect(True)
        r.attributes("-topmost", True, "-alpha", 0.0)
        r.configure(bg="#111111")
        top = tk.Frame(r, bg="#111111"); top.pack(padx=10, pady=(8, 0), anchor="w", fill="x")
        self.dot = tk.Label(top, text="●", bg="#111111", font=("Segoe UI", 11)); self.dot.pack(side="left")
        self.head = tk.Label(top, fg="#bbbbbb", bg="#111111", font=("Segoe UI", 10)); self.head.pack(side="left", padx=(4, 0))
        self.bar = tk.Canvas(r, width=200, height=4, bg="#222222", highlightthickness=0)
        self.bar.pack(padx=10, pady=(4, 0), anchor="w")
        self.fill = self.bar.create_rectangle(0, 0, 0, 4, fill="#44cc66", width=0)
        self.raw = tk.Label(r, fg="#888888", bg="#111111", wraplength=680, justify="left", font=("Segoe UI", 11))
        self.cleaned = tk.Label(r, fg="#ffffff", bg="#111111", wraplength=680, justify="left", font=("Segoe UI", 12))
        self.raw.pack(padx=10, anchor="w")
        self.cleaned.pack(padx=10, pady=(0, 8), anchor="w")
        self._place()
        r.deiconify(); r.update()
        hwnd = win32gui.GetParent(r.winfo_id()) or r.winfo_id()
        ex = win32gui.GetWindowLong(hwnd, -20)
        # NOACTIVATE|TOOLWINDOW|TRANSPARENT: never takes focus, no taskbar entry, click-through
        win32gui.SetWindowLong(hwnd, -20, ex | 0x08000000 | 0x80 | 0x20)
        try:
            win32gui.SetForegroundWindow(prev)
        except Exception:
            pass

    def _place(self):
        self.root.update_idletasks()
        h = self.root.winfo_reqheight()
        self.root.geometry(f"{self.W}x{h}+{(self.root.winfo_screenwidth() - self.W) // 2}+{self.root.winfo_screenheight() - h - 90}")

    def post(self, fn):
        self.q.put(fn)

    def _run_task(self, fn):
        try:
            fn()
        except Exception:
            logging.getLogger("murmur").exception("overlay task failed")

    def _tick(self):
        while not self.q.empty():
            self._run_task(self.q.get())
        if not self.recording:
            return
        self.bar.coords(self.fill, 0, 0, min(1.0, self.level * 10) * 200, 4)

    def run(self):
        def poll():
            try:
                self._tick()
            finally:
                self.root.after(50, poll)
        poll()
        self.root.mainloop()

    def header(self, color, text):
        self.dot.config(fg=color)
        self.head.config(text=text)
        self._place()

    def show(self, color, text):
        self.recording = True
        self.raw.config(text=""); self.cleaned.config(text="")
        self.header(color, text)
        self.root.attributes("-alpha", 0.92)

    def update(self, raw, cleaned):
        self.raw.config(text=raw)
        self.cleaned.config(text=cleaned)
        self._place()

    def hide(self):
        self.recording = False
        self.root.attributes("-alpha", 0.0)
